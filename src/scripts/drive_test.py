"""
Ручной тест движения робота FRANCY.

Отправляет команды моторам и показывает телеметрию. Результат проверяется визуально.

Запуск:
    python3 scripts/drive_test.py

Порт по умолчанию: /dev/ttyUSB0
Переопределить:
    SERIAL_PORT=/dev/ttyUSB1 python3 scripts/drive_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.serial_link import SerialLink
from pilot.compass import Compass

PORT = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")
SPEED_FWD  = 200  # 128 + 40  (~30% мощности вперёд)
SPEED_BWD  = 55   # 128 - 40  (~30% мощности назад)
SPEED_STOP = 128


def print_telemetry(link: SerialLink, compass: Compass, label: str):
    t = link.get_telemetry()
    heading = compass.heading(t.mag_x, t.mag_y)
    rf = f"{t.range_mm:4d}mm" if t.range_mm > 0 else "  --  "
    print(f"  [{label}] SQ:{t.sq:5d}  heading:{heading:6.1f}°  "
          f"L:{t.left_speed:+4d} R:{t.right_speed:+4d}  "
          f"RF:{rf}  V:{t.voltage:.1f}V")


def step(link: SerialLink, compass: Compass, label: str,
         left: int, right: int, duration: float):
    print(f"\n→ {label}")
    link.set_motors(left, right)
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        if link.wait_for_packet(timeout=0.3):
            print_telemetry(link, compass, label)
    link.set_motors(SPEED_STOP, SPEED_STOP)
    time.sleep(0.3)


def main():
    print(f"Подключение к {PORT}...")
    link = SerialLink(PORT)

    if not link.wait_for_packet(timeout=5.0):
        print("Ошибка: нет телеметрии. Проверь подключение и прошивку.")
        link.close()
        sys.exit(1)

    compass = Compass()
    print("Соединение установлено. Начинаем тест.")
    print("Ctrl+C для экстренной остановки.\n")
    link.beep(200)
    print_telemetry(link, compass, "старт")

    try:
        step(link, compass, "ВПЕРЁД 2 сек",
             SPEED_FWD, SPEED_FWD, duration=2.0)

        step(link, compass, "СТОП 1 сек",
             SPEED_STOP, SPEED_STOP, duration=1.0)

        step(link, compass, "НАЗАД 2 сек",
             SPEED_BWD, SPEED_BWD, duration=2.0)

        step(link, compass, "СТОП 1 сек",
             SPEED_STOP, SPEED_STOP, duration=1.0)

        step(link, compass, "ПОВОРОТ ВПРАВО 2 сек",
             SPEED_FWD, SPEED_BWD, duration=2.0)

        step(link, compass, "СТОП 1 сек",
             SPEED_STOP, SPEED_STOP, duration=1.0)

        step(link, compass, "ПОВОРОТ ВЛЕВО 2 сек",
             SPEED_BWD, SPEED_FWD, duration=2.0)

        step(link, compass, "СТОП",
             SPEED_STOP, SPEED_STOP, duration=1.0)

        print("\nТест завершён.")
        print_telemetry(link, compass, "финал")

    except KeyboardInterrupt:
        print("\n\nЭкстренная остановка!")
        link.set_motors(SPEED_STOP, SPEED_STOP)
    finally:
        link.close()


if __name__ == "__main__":
    main()
