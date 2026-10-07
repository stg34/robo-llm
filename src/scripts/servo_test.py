"""
Тест и калибровка серво-пушки.

Режимы:
    python scripts/servo_test.py                     # одиночный выстрел
    python scripts/servo_test.py --count 3           # 3 выстрела подряд
    python scripts/servo_test.py --pos 90            # выставить серво на 90°
    python scripts/servo_test.py --calibrate         # интерактивная калибровка углов

Опции:
    --port PORT    Serial port (по умолчанию из $SERIAL_PORT или /dev/ttyUSB0)

Калибровка (--calibrate):
    Введите угол 0-180 и нажмите Enter — серво переместится.
    Найдите:
      RELEASE_POS — курок отпущен, серво не давит
      FIRE_POS    — курок нажат до конца, выстрел происходит
    Введите 'f' для тестового выстрела, 'q' для выхода.
    Найденные значения нужно прописать в body/servo_gun.h.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.serial_link import SerialLink


def get_port(args) -> str:
    return args.port or os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")


def connect(port: str) -> SerialLink:
    print(f"Подключение к {port}...", end=" ", flush=True)
    link = SerialLink(port)
    if not link.wait_for_packet(timeout=5.0):
        print("ОШИБКА: нет телеметрии от робота.")
        link.close()
        sys.exit(1)
    t = link.get_telemetry()
    print(f"OK  (V={t.voltage:.1f}V)")
    return link


def do_fire(link: SerialLink, n: int = 1):
    for i in range(n):
        if n > 1:
            print(f"Выстрел {i+1}/{n}...", end=" ", flush=True)
        else:
            print("Выстрел...", end=" ", flush=True)

        for _ in range(3):
            link.beep(250)
            time.sleep(1.0)  # 250ms beep + 750ms pause

        t0 = time.time()
        link.send_fire()
        code = link.wait_for_done(timeout=5.0)
        elapsed = time.time() - t0
        status = "OK" if code == "OK" else f"ОШИБКА ({code})"
        print(f"{status}  ({elapsed:.2f}с)")
        if i < n - 1:
            time.sleep(0.5)


def do_set_pos(link: SerialLink, degrees: int):
    print(f"Серво → {degrees}°")
    link.send_servo_pos(degrees)
    time.sleep(0.1)  # дать серво начать движение


def do_calibrate(link: SerialLink):
    print()
    print("=== Режим калибровки ===")
    print("Введите угол (0-180) → серво переместится")
    print("  f  — тестовый выстрел (полный цикл)")
    print("  q  — выход")
    print()
    print("Текущие значения в servo_gun.h:")
    print("  RELEASE_POS = 45   (курок отпущен)")
    print("  FIRE_POS    = 150  (курок нажат)")
    print()

    while True:
        try:
            raw = input("Угол [0-180] / f / q: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if raw == 'q':
            break
        elif raw == 'f':
            do_fire(link)
        else:
            try:
                deg = int(raw)
            except ValueError:
                print("  Введите число 0-180, 'f' или 'q'")
                continue
            if not 0 <= deg <= 180:
                print("  Диапазон: 0-180")
                continue
            do_set_pos(link, deg)

    print()
    print("Запишите найденные значения в body/servo_gun.h:")
    print("  static const int RELEASE_POS = ???;")
    print("  static const int FIRE_POS    = ???;")


def main():
    parser = argparse.ArgumentParser(description="Тест и калибровка серво-пушки")
    parser.add_argument("--port", default="", help="Serial port")
    parser.add_argument("--count", type=int, default=1, help="Количество выстрелов")
    parser.add_argument("--pos", type=int, default=None, metavar="DEG",
                        help="Выставить серво на DEG градусов (0-180)")
    parser.add_argument("--calibrate", action="store_true",
                        help="Интерактивная калибровка углов")
    args = parser.parse_args()

    port = get_port(args)
    link = connect(port)

    try:
        if args.calibrate:
            do_calibrate(link)
        elif args.pos is not None:
            do_set_pos(link, args.pos)
        else:
            do_fire(link, args.count)
    finally:
        link.close()


if __name__ == "__main__":
    main()
