"""
Карта магнитных аномалий.

Выводит heading компаса в реальном времени пока робот неподвижен.
Двигайте робота по квартире и наблюдайте как меняется heading.

Запуск:
    python3 scripts/mag_map.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.serial_link import SerialLink
from pilot.compass import Compass

PORT = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")


def main():
    print(f"Подключение к {PORT}...")
    link = SerialLink(PORT)

    if not link.wait_for_packet(timeout=5.0):
        print("Ошибка: нет телеметрии.")
        link.close()
        sys.exit(1)

    compass = Compass()
    print("Двигайте робота по квартире. Ctrl+C для остановки.\n")
    print(f"{'t,s':>7}  {'heading,°':>10}  {'mag_x':>8}  {'mag_y':>8}")
    print("-" * 42)

    start = time.monotonic()
    last_sq = -1

    try:
        while True:
            link.wait_for_packet(timeout=0.5)
            t = link.get_telemetry()

            if t.sq == last_sq:
                continue
            last_sq = t.sq

            h = compass.heading(t.mag_x, t.mag_y)
            elapsed = time.monotonic() - start
            print(f"{elapsed:7.1f}  {h:10.1f}  {t.mag_x:8.1f}  {t.mag_y:8.1f}")

    except KeyboardInterrupt:
        print("\nГотово.")
    finally:
        link.close()


if __name__ == "__main__":
    main()
