from pilot.compass import Compass
from pilot.serial_link import SerialLink


class Navigator:
    """High-level motion interface. All PD control runs in firmware (50 Hz).

    turn_relative / move → send T/M command to Arduino, wait for D: response.
    turn_absolute         → compute delta from compass, delegate to turn_relative.
    stop                  → send immediate L128/R128 (interrupts any motion).

    Gyro bias calibration is performed by the Arduino on startup (calibrate(30)).
    """

    def __init__(
        self,
        serial_link: SerialLink,
        compass: Compass,
        timeout: float = 15.0,
        calibrate_gyro: bool = False,   # kept for API compat; calibration now in firmware
    ):
        self._link = serial_link
        self._compass = compass
        self._timeout = timeout

        if calibrate_gyro:
            # Legacy Python-side calibration; firmware does its own on startup.
            print("Калибровка гироскопа (~3с), не двигайте робота...", end=" ", flush=True)
            bias = self._link.calibrate_gyro(n_packets=30)
            print(f"bias={bias:+.2f}°/s")

    def turn_relative(self, degrees: float) -> dict:
        """Turn by degrees relative to current heading. Positive = clockwise.

        Delegates to firmware motion controller (T command).
        Returns dict: status, heading (compass after settle).
        """
        self._link.send_turn(degrees)
        code = self._link.wait_for_done(timeout=self._timeout + 3.0)

        turned_deg = self._link.get_done_angle()
        signed_deg = turned_deg if degrees >= 0 else -turned_deg
        return {
            "status": "ok" if code == "OK" else code.lower(),
            "turned_deg": round(signed_deg, 1),
        }

    def turn_absolute(self, heading: float) -> dict:
        """Turn to an absolute compass heading. 0=north, 90=east, clockwise."""
        self._link.wait_for_packet(timeout=0.5)
        t = self._link.get_telemetry()
        current = self._compass.heading(t.mag_x, t.mag_y)
        delta = ((heading - current + 180) % 360) - 180   # shortest path, -180..+180
        return self.turn_relative(delta)

    def stop(self) -> dict:
        """Immediate stop. Overrides firmware motion controller."""
        self._link.set_motors(128, 128)
        return {"status": "stopped"}

    def move(self, meters: float) -> dict:
        """Move forward (meters > 0) or backward (meters < 0) using rangefinder.

        Delegates to firmware motion controller (M command).
        Returns dict: status, range_mm (current reading after settle), moved_mm.
        """
        if meters == 0:
            return {"status": "ok", "moved_mm": 0, "range_mm": 0}

        # Snapshot initial range for moved_mm reporting
        self._link.wait_for_packet(timeout=0.5)
        initial_range = self._link.get_telemetry().range_mm

        mm = int(meters * 1000)
        self._link.send_move(mm)
        code = self._link.wait_for_done(timeout=self._timeout + 3.0)

        self._link.wait_for_packet(timeout=0.5)
        t = self._link.get_telemetry()
        moved = initial_range - t.range_mm

        return {
            "status": "ok" if code == "OK" else code.lower(),
            "range_mm": t.range_mm,
            "moved_mm": moved,
        }
