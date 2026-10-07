import dataclasses
import statistics
import threading
import time
from collections import deque

import serial

from pilot.telemetry import TelemetryFrame


class SerialLink:
    # Median filter window for gyro_z.
    # Window=3 → kills isolated spikes (e.g. I2C glitches) with 1-packet latency.
    # Window=1 → disables median filtering (raw value, only bias subtracted).
    _GYRO_WINDOW = 3

    def __init__(self, port: str, baudrate: int = 57600):
        self._port = serial.Serial(port, baudrate, timeout=0.1, write_timeout=2.0)
        self._lock = threading.Lock()
        self._telemetry = TelemetryFrame()
        self._packet_event = threading.Event()
        self._done_event = threading.Event()
        self._done_result: str = ''
        self._done_angle: float = 0.0
        self._stop_event = threading.Event()
        self._gyro_window: deque[float] = deque(maxlen=self._GYRO_WINDOW)
        self._gyro_bias: float = 0.0
        self._telemetry_callback = None   # вызывается из фонового потока при каждом пакете
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def set_telemetry_callback(self, callback) -> None:
        """Зарегистрировать callback(ts: float, t: TelemetryFrame).

        Вызывается из фонового потока при каждом разобранном пакете (~10 Гц).
        Используется для плотного логирования телеметрии в сессионный файл.
        """
        self._telemetry_callback = callback

    def calibrate_gyro(self, n_packets: int = 30) -> float:
        """Collect n_packets while robot is stationary, estimate and store gyro bias.

        Must be called before any movement. Returns measured bias in °/s.
        Uses median to exclude spike influence from the estimate.
        """
        samples: list[float] = []
        for _ in range(n_packets):
            self.wait_for_packet(timeout=0.5)
            samples.append(self.get_telemetry().gyro_z)
        # Median is robust to remaining spikes; store as bias offset
        self._gyro_bias = statistics.median(samples)
        return self._gyro_bias

    def get_telemetry(self) -> TelemetryFrame:
        with self._lock:
            return dataclasses.replace(self._telemetry)

    def wait_for_packet(self, timeout: float = 1.0) -> bool:
        """Block until a new packet is parsed. Returns False on timeout."""
        self._packet_event.clear()
        return self._packet_event.wait(timeout)

    def set_motors(self, left: int, right: int):
        """Send motor commands. left/right: 0-255, 128 = stop."""
        left = max(0, min(255, left))
        right = max(0, min(255, right))
        self._port.write(f"L{left:03d}\nR{right:03d}\n".encode())

    def send_gyro_bias_correction(self, bias_dps: float) -> None:
        """Send gyro bias adjustment to firmware after Python warm-sensor calibration.

        bias_dps: Python-measured firmware output at rest (°/s).
        Firmware adds this value to its internal _bias, zeroing out thermal drift.
        Format: G±DDDD\\n where DDDD = |bias_dps| * 10 (tenths of °/s).
        """
        sign = '+' if bias_dps >= 0 else '-'
        tenths = min(int(abs(bias_dps) * 10 + 0.5), 9999)
        self._port.write(f"G{sign}{tenths:04d}\n".encode())

    def send_turn(self, degrees: float) -> None:
        """Send turn command to firmware motion controller.

        degrees > 0 = CW, degrees < 0 = CCW.
        Format: T+DDDD\\n or T-DDDD\\n (4 digits, zero-padded, max 9999).
        Call wait_for_done() to block until the turn completes.
        """
        sign = '+' if degrees >= 0 else '-'
        val = min(int(abs(degrees) + 0.5), 9999)
        self._done_event.clear()
        self._port.write(f"T{sign}{val:04d}\n".encode())

    def send_move(self, mm: int) -> None:
        """Send move command to firmware motion controller.

        mm > 0 = forward, mm < 0 = backward.
        Format: M+DDDD\\n or M-DDDD\\n (4 digits, zero-padded, max 9999 mm).
        Call wait_for_done() to block until the move completes.
        """
        sign = '+' if mm >= 0 else '-'
        val = min(abs(mm), 9999)
        self._done_event.clear()
        self._port.write(f"M{sign}{val:04d}\n".encode())

    def wait_for_done(self, timeout: float = 20.0) -> str:
        """Block until firmware sends D:<code> response or timeout expires.

        Returns result code: 'OK', 'TO' (timeout), 'ST' (stuck),
        'OB' (obstacle), 'JM' (rangefinder jump), or 'TO' on Python timeout.
        Use get_done_angle() after this call to retrieve gyro-accumulated degrees.
        """
        if self._done_event.wait(timeout):
            return self._done_result
        return 'TO'

    def get_done_angle(self) -> float:
        """Gyro-accumulated degrees from the last completed turn (0.0 for moves)."""
        return self._done_angle

    def send_servo_pos(self, degrees: int) -> None:
        """Move servo to an arbitrary position (calibration). No completion response."""
        degrees = max(0, min(180, degrees))
        self._port.write(f"S{degrees:03d}\n".encode())

    def set_leds(self, red: bool, blue: bool) -> None:
        """Set both decorative LEDs atomically. Format: E<R><B>\\n."""
        r = '1' if red else '0'
        b = '1' if blue else '0'
        self._port.write(f"E{r}{b}\n".encode())

    def send_fire(self) -> None:
        """Send fire command to servo gun firmware.

        Clears the done event before sending so wait_for_done() can be used
        to block until the firmware sends D:OK after the servo cycle completes.
        """
        self._done_event.clear()
        self._port.write(b"F\n")

    def beep(self, duration_ms: int = 250) -> float:
        """Send piezo beep command. Returns time.time() at moment of send.

        duration_ms: 0-999 ms. Used as a sync marker for post-processing.
        """
        duration_ms = max(0, min(999, duration_ms))
        self._port.write(f"P{duration_ms:03d}\n".encode())
        return time.time()

    def close(self):
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self._port.close()

    def _read_loop(self):
        buffer = []
        in_frame = False

        while not self._stop_event.is_set():
            try:
                raw = self._port.readline()
            except Exception:
                continue

            if not raw:
                continue

            line = raw.decode('ascii', errors='ignore').strip()

            # Motion-done response from firmware (outside telemetry frames)
            # Format: D:<code>:<angle>\n  e.g. D:OK:89.7
            if line.startswith('D:'):
                parts = line[2:].split(':')
                self._done_result = parts[0]          # 'OK', 'TO', 'ST', 'OB', 'JM'
                try:
                    self._done_angle = float(parts[1]) if len(parts) > 1 else 0.0
                except ValueError:
                    self._done_angle = 0.0
                self._done_event.set()
                continue

            if line == '---':
                buffer = []
                in_frame = True
            elif line == '***':
                if in_frame:
                    self._parse_frame(buffer)
                in_frame = False
                buffer = []
            elif in_frame:
                buffer.append(line)

    def _parse_frame(self, lines: list[str]):
        fields = {}
        for line in lines:
            if ':' in line:
                key, _, value = line.partition(':')
                fields[key] = value

        if 'SQ' not in fields:
            return

        def _int(key, default=0):
            try:
                return int(fields[key]) if fields.get(key, '') != '' else default
            except (ValueError, KeyError):
                return default

        def _float(key, default=0.0):
            try:
                return float(fields[key]) if fields.get(key, '') != '' else default
            except (ValueError, KeyError):
                return default

        # Gyro spike filter: median of last _GYRO_WINDOW raw readings.
        # Isolated spikes (e.g. I2C glitch producing ±17°/s for one packet)
        # are replaced by the median of neighbours.
        raw_gyro = _float('GZ')
        self._gyro_window.append(raw_gyro)
        filtered_gyro = statistics.median(self._gyro_window) - self._gyro_bias

        with self._lock:
            prev = self._telemetry
            sq = _int('SQ')

            lost = 0
            if prev.timestamp > 0:
                expected = (prev.sq + 1) % 65536
                lost = (sq - expected) % 65536

            ts_wall = time.time()
            self._telemetry = TelemetryFrame(
                sq=sq,
                left_speed=_int('LS'),
                right_speed=_int('RS'),
                left_current=_int('LC'),
                right_current=_int('RC'),
                mag_x=_float('GX'),
                mag_y=_float('GY'),
                voltage=_float('V'),
                range_mm=_int('RF'),
                gyro_z=filtered_gyro,
                timestamp=time.monotonic(),
                packets_lost=prev.packets_lost + lost,
                led_red=bool(_int('LR')),
                led_blue=bool(_int('LB')),
            )

        if self._telemetry_callback is not None:
            try:
                self._telemetry_callback(ts_wall, self._telemetry)
            except Exception:
                pass

        self._packet_event.set()
