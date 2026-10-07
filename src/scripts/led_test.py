"""
Тест управления декоративными LED-ами.

Режимы:
    python scripts/led_test.py              # blink-тест: мигает каждым LED по очереди
    python scripts/led_test.py --red        # включить только красный
    python scripts/led_test.py --blue       # включить только синий
    python scripts/led_test.py --both       # включить оба
    python scripts/led_test.py --off        # выключить оба
    python scripts/led_test.py --raw ER1    # послать сырую команду (без \n)

Опции:
    --port PORT    Serial port (по умолчанию из $SERIAL_PORT или /dev/ttyUSB0)
    --no-telem     не ждать подтверждения по телеметрии

Для диагностики смотри колонки LR/LB в телеметрии — они должны
обновляться после каждой команды.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.serial_link import SerialLink


def get_port(args) -> str:
    return args.port or os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")


def connect(port: str, wait_telem: bool = True) -> SerialLink:
    print(f"Подключение к {port}...", end=" ", flush=True)
    link = SerialLink(port)
    if wait_telem:
        if not link.wait_for_packet(timeout=5.0):
            print("ОШИБКА: нет телеметрии от робота.")
            link.close()
            sys.exit(1)
        t = link.get_telemetry()
        print(f"OK  (V={t.voltage:.1f}V, LR={int(t.led_red)}, LB={int(t.led_blue)})")
    else:
        print("OK (без телеметрии)")
    return link


def set_leds(link: SerialLink, red: bool, blue: bool, wait_telem: bool = True):
    link.set_leds(red, blue)
    if wait_telem:
        # Пауза: Arduino обрабатывает команду за один loop (10ms),
        # телеметрия раз в 100ms — 150ms гарантирует свежий пакет с новым состоянием.
        time.sleep(0.4)
        link.wait_for_packet(timeout=0.5)
        t = link.get_telemetry()
        print(f"  → отправлено red={int(red)} blue={int(blue)} | "
              f"телеметрия LR={int(t.led_red)} LB={int(t.led_blue)}", end="")
        ok = (t.led_red == red and t.led_blue == blue)
        print("  ✓" if ok else "  ✗ НЕСОВПАДЕНИЕ")
    else:
        print(f"  → red={int(red)} blue={int(blue)}")


def do_blink(link: SerialLink, wait_telem: bool):
    print("\n=== Blink-тест ===")
    print("Последовательность: красный → синий → оба → выкл → повтор 3 раза")
    print("Ctrl+C для прерывания\n")

    steps = [
        (True,  False, "красный"),
        (False, True,  "синий"),
        (True,  True,  "оба"),
        (False, False, "выкл"),
    ]

    try:
        for rep in range(3):
            print(f"Цикл {rep + 1}/3:")
            for red, blue, label in steps:
                print(f"  {label:<12}", end="", flush=True)
                set_leds(link, red, blue, wait_telem)
                time.sleep(0.8)
    except KeyboardInterrupt:
        print("\nПрервано.")
    finally:
        print("\nВыключаю оба LED...")
        link.set_leds(False, False)


def do_raw(link: SerialLink, cmd: str):
    raw = (cmd + "\n").encode()
    print(f"Отправка: {raw!r}")
    link._port.write(raw)
    time.sleep(0.15)
    link.wait_for_packet(timeout=0.5)
    t = link.get_telemetry()
    print(f"Телеметрия: LR={int(t.led_red)} LB={int(t.led_blue)}")


def main():
    parser = argparse.ArgumentParser(description="Тест LED-подсветки робота")
    parser.add_argument("--port", default="", help="Serial port")
    parser.add_argument("--no-telem", action="store_true",
                        help="Не ждать подтверждения по телеметрии")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--red",  action="store_true", help="Включить красный, выкл синий")
    mode.add_argument("--blue", action="store_true", help="Включить синий, выкл красный")
    mode.add_argument("--both", action="store_true", help="Включить оба")
    mode.add_argument("--off",  action="store_true", help="Выключить оба")
    mode.add_argument("--raw",  metavar="CMD",       help="Сырая команда без \\n (например: ER1)")
    args = parser.parse_args()

    port = get_port(args)
    wait_telem = not args.no_telem
    link = connect(port, wait_telem)

    try:
        if args.raw:
            do_raw(link, args.raw)
        elif args.red:
            print("Красный ON, синий OFF:")
            set_leds(link, True, False, wait_telem)
        elif args.blue:
            print("Синий ON, красный OFF:")
            set_leds(link, False, True, wait_telem)
        elif args.both:
            print("Оба ON:")
            set_leds(link, True, True, wait_telem)
        elif args.off:
            print("Оба OFF:")
            set_leds(link, False, False, wait_telem)
        else:
            do_blink(link, wait_telem)
    finally:
        link.close()


if __name__ == "__main__":
    main()
