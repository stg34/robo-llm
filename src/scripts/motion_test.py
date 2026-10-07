"""
Тест прецизионного движения: одна команда (move или turn) с выводом телеметрии.

Использование:
    python3 scripts/motion_test.py --move 1.0          # вперёд 1 м
    python3 scripts/motion_test.py --move -0.5         # назад 0.5 м
    python3 scripts/motion_test.py --turn 70           # поворот CW 70°
    python3 scripts/motion_test.py --turn -45          # поворот CCW 45°
    python3 scripts/motion_test.py --move 1.0 --turn 70  # движение, потом поворот
    python3 scripts/motion_test.py --gyro-drift 5      # дрейф гироскопа: 5с покой + 5с моторы

Опции:
    --port  PORT        Serial port (по умолчанию из $SERIAL_PORT или /dev/ttyUSB0)
    --verbose           Печатать каждый пакет телеметрии во время выполнения
    --motor-speed N     Скорость моторов для --gyro-drift (0-127, default=55)
"""

import argparse
import math
import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.serial_link import SerialLink
from pilot.compass import Compass
from pilot.navigator import Navigator


# ---------------------------------------------------------------------------
# Telemetry printer
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()
_verbose = False
_motion_active = False


def _telemetry_callback(ts_wall: float, t):
    if not _verbose or not _motion_active:
        return
    with _print_lock:
        print(
            f"  telem | heading={Compass().heading(t.mag_x, t.mag_y):6.1f}°  "
            f"range={t.range_mm:5d}mm  "
            f"gyro={t.gyro_z:+7.1f}°/s  "
            f"L={t.left_speed:+4d} R={t.right_speed:+4d}  "
            f"V={t.voltage:.2f}V",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _verbose, _motion_active

    parser = argparse.ArgumentParser(description="Motion accuracy test")
    parser.add_argument("--move",    type=float, metavar="METERS",
                        help="Move forward (positive) or backward (negative) N meters")
    parser.add_argument("--turn",    type=float, metavar="DEGREES",
                        help="Turn CW (positive) or CCW (negative) N degrees")
    parser.add_argument("--gyro-drift", type=float, metavar="SECONDS",
                        help="Gyro drift test: SECONDS at rest, then SECONDS with motors running")
    parser.add_argument("--motor-speed", type=int, default=55, metavar="N",
                        help="Motor speed for --gyro-drift and --motor-test (0-127, default=55)")
    parser.add_argument("--motor-test", action="store_true",
                        help="Диагностика: гоняет каждый мотор вперёд/назад по 1.5с")
    parser.add_argument("--gyro-watch", action="store_true",
                        help="Стримить gyro_z в реальном времени (крути робота руками)")
    parser.add_argument("--port",    default=os.environ.get("SERIAL_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--calibrate", action="store_true",
                        help="Calibrate gyro bias from Python side (~3s, robot must be still)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every telemetry packet during motion")
    args = parser.parse_args()

    if args.move is None and args.turn is None and args.gyro_drift is None \
            and not args.motor_test and not args.gyro_watch:
        parser.error("Укажи хотя бы одно: --move, --turn, --gyro-drift, --motor-test или --gyro-watch")

    _verbose = args.verbose

    print(f"Подключение к {args.port}...")
    link = SerialLink(args.port)

    if not link.wait_for_packet(timeout=5.0):
        print("Ошибка: нет телеметрии от робота.")
        link.close()
        sys.exit(1)

    compass = Compass()
    nav = Navigator(link, compass)
    link.set_telemetry_callback(_telemetry_callback)

    if args.calibrate:
        print("Калибровка гироскопа (~3с), не двигайте робота...", end=" ", flush=True)
        bias = link.calibrate_gyro(n_packets=30)
        link.send_gyro_bias_correction(bias)
        print(f"bias={bias:+.2f}°/s → отправлено в firmware")

    link.beep(150)
    time.sleep(0.1)
    t = link.get_telemetry()
    heading0 = compass.heading(t.mag_x, t.mag_y)

    print(f"Начальное состояние: heading={heading0:.1f}°  range={t.range_mm}mm  V={t.voltage:.2f}V")
    print()

    try:
        if args.gyro_watch:
            print("Крути робота руками. CW = вправо сверху. Ctrl+C для выхода.\n")
            while True:
                link.wait_for_packet(timeout=0.5)
                gz = link.get_telemetry().gyro_z
                bar = int(abs(gz) / 3)
                direction = "← CCW" if gz > 0 else "CW →" if gz < 0 else "  стоп"
                print(f"  gyro_z = {gz:+7.2f}°/s  {direction}  {'█' * min(bar, 30)}",
                      end="\r", flush=True)

        if args.motor_test:
            _motor_test(link, args.motor_speed)
            return

        if args.gyro_drift is not None:
            _gyro_drift_test(link, args.gyro_drift, args.motor_speed)
            return

        if args.move is not None:
            print(f"→ Движение {'вперёд' if args.move > 0 else 'назад'} {abs(args.move):.2f} м")
            t0 = time.monotonic()
            _motion_active = True
            result = nav.move(args.move)
            _motion_active = False
            elapsed = time.monotonic() - t0

            print(f"\nРезультат:  status={result['status']}  "
                  f"moved={result.get('moved_mm', '?')}mm  "
                  f"range={result.get('range_mm', '?')}mm  "
                  f"время={elapsed:.2f}с")
            target_mm = int(abs(args.move) * 1000)
            actual_mm = abs(result.get('moved_mm') or 0)
            if result['status'] == 'ok' and target_mm > 0:
                error_mm = actual_mm - target_mm
                print(f"Точность:   цель={target_mm}mm  факт={actual_mm}mm  "
                      f"ошибка={error_mm:+d}mm  ({100*error_mm/target_mm:+.1f}%)")

        if args.turn is not None:
            print(f"\n→ Поворот {'CW' if args.turn > 0 else 'CCW'} {abs(args.turn):.1f}°")
            t0 = time.monotonic()
            _motion_active = True
            result = nav.turn_relative(args.turn)
            _motion_active = False
            elapsed = time.monotonic() - t0

            gyro_deg = result.get('turned_deg', 0.0)
            gyro_error = gyro_deg - args.turn
            print(f"\nРезультат:  status={result['status']}  время={elapsed:.2f}с")
            print(f"Точность:   цель={args.turn:+.1f}°  факт={gyro_deg:+.1f}°  "
                  f"ошибка={gyro_error:+.1f}°")

    except KeyboardInterrupt:
        print("\nОстановка!")
        nav.stop()
    finally:
        link.beep(100)
        link.close()


def _motor_test(link: 'SerialLink', speed: int) -> None:
    """Диагностика проводки: каждый мотор вперёд и назад по 1.5с."""
    STOP  = 128
    FWD   = STOP + speed
    BWD   = STOP - speed
    DELAY = 1.5

    steps = [
        ("ЛЕВЫЙ  ВПЕРЁД",  FWD,  STOP),
        ("ЛЕВЫЙ  НАЗАД",   BWD,  STOP),
        ("ПРАВЫЙ ВПЕРЁД",  STOP, FWD),
        ("ПРАВЫЙ НАЗАД",   STOP, BWD),
    ]

    print(f"\n=== Тест моторов (speed={speed}) ===")
    print("Смотри на робота сверху. Каждый шаг — 1.5с, потом стоп.\n")
    for label, left, right in steps:
        input(f"  Нажми Enter для: {label} ...")
        link.set_motors(left, right)
        time.sleep(DELAY)
        link.set_motors(STOP, STOP)
        time.sleep(0.3)
    print("\nГотово.")


def _gyro_drift_test(link: 'SerialLink', seconds: float, motor_speed: int) -> None:
    """Measure gyro drift at rest and with motors running."""
    MOTOR_NEUTRAL = 128

    def _collect(label: str, duration: float) -> list[float]:
        """Collect gyro_z samples for `duration` seconds, print each one."""
        samples: list[float] = []
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            link.wait_for_packet(timeout=0.5)
            gz = link.get_telemetry().gyro_z
            samples.append(gz)
            remaining = deadline - time.monotonic()
            print(f"  {label}  gyro_z={gz:+7.2f}°/s  [{remaining:4.1f}с осталось]",
                  flush=True)
        return samples

    def _report(label: str, samples: list[float]) -> float:
        n = len(samples)
        if n == 0:
            return 0.0
        mean = sum(samples) / n
        variance = sum((x - mean) ** 2 for x in samples) / n
        std = math.sqrt(variance)
        # Integrate at 10 Hz (100ms per packet)
        integrated_deg = mean * (n * 0.1)
        print(f"\n  {label}:")
        print(f"    Образцов:            {n}")
        print(f"    Среднее:             {mean:+.3f}°/s")
        print(f"    СКО:                 {std:.3f}°/s")
        print(f"    Накопленный угол:    {integrated_deg:+.1f}° за {n*0.1:.1f}с")
        return mean

    motor_speed = max(0, min(127, motor_speed))
    fwd_cmd = MOTOR_NEUTRAL + motor_speed  # e.g. 128+55=183

    print(f"\n=== Тест дрейфа гироскопа ({seconds:.0f}с покой + {seconds:.0f}с моторы speed={motor_speed}) ===\n")

    print(f"Фаза 1: ПОКОЙ ({seconds:.0f}с) — не двигайте робота")
    samples_rest = _collect("покой", seconds)
    mean_rest = _report("Покой", samples_rest)

    print(f"\nФаза 2: МОТОРЫ ({seconds:.0f}с, speed={motor_speed}) — робот поедет вперёд!")
    link.set_motors(fwd_cmd, fwd_cmd)
    samples_motors = _collect("моторы", seconds)
    link.set_motors(MOTOR_NEUTRAL, MOTOR_NEUTRAL)
    mean_motors = _report("Моторы", samples_motors)

    drift_from_vibration = mean_motors - mean_rest
    print(f"\n  Дрейф от вибрации:   {drift_from_vibration:+.3f}°/s")
    print(f"  (за 1м при ~500мм/с≈2с: накопится ~{drift_from_vibration*2:+.1f}°)\n")


if __name__ == "__main__":
    main()
