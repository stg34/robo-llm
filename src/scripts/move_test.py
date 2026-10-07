"""
Тест navigator.move() — движение по дальномеру.

Запуск:
    python3 scripts/move_test.py

Перед запуском: поставить робота перед препятствием на расстоянии 1-3 метра.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.serial_link import SerialLink
from pilot.compass import Compass
from pilot.navigator import Navigator

PORT = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")


def main():
    print(f"Подключение к {PORT}...")
    link = SerialLink(PORT)

    if not link.wait_for_packet(timeout=5.0):
        print("Ошибка: нет телеметрии.")
        link.close()
        sys.exit(1)

    compass = Compass()
    nav = Navigator(link, compass)

    link.beep(200)
    t = link.get_telemetry()
    initial_heading = compass.heading(t.mag_x, t.mag_y)
    print(f"Готов. Дистанция: {t.range_mm}mm  Heading: {initial_heading:.1f}°")
    print("Ctrl+C для остановки.\n")

    try:
        print("→ Вперёд 0.3 м")
        result = nav.move(0.3)
        print(f"  {result}")

        print("→ Назад 0.3 м")
        result = nav.move(-0.3)
        print(f"  {result}")

        print("→ Вперёд 1 м")
        result = nav.move(1.0)
        print(f"  {result}")

        print("→ Назад 1 м")
        result = nav.move(-1.0)
        print(f"  {result}")

        # print("→ Поворот +45°")
        # result = nav.turn_relative(45)
        # print(f"  {result}")
        #
        # print("→ Поворот -45°")
        # result = nav.turn_relative(-45)
        # print(f"  {result}")

        print("\nТест завершён. Жду 3с для стабилизации компаса...")
        time.sleep(3.0)
        t = link.get_telemetry()
        final_h = compass.heading(t.mag_x, t.mag_y)
        print(f"Финальный heading: {final_h:.1f}°  начальный: {initial_heading:.1f}°  Δ={final_h - initial_heading:+.1f}°")

    except KeyboardInterrupt:
        print("\nОстановка!")
        nav.stop()
    finally:
        link.close()


if __name__ == "__main__":
    main()
