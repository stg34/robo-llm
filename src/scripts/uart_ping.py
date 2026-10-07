#!/usr/bin/env python3
"""
uart_ping.py — проверка USB-UART конвертера через Arduino Leonardo.

Схема подключения:
  TX конвертера → pin 0 (RX1) Leonardo
  RX конвертера → pin 1 (TX1) Leonardo
  GND конвертера → GND Leonardo

Использование:
  python scripts/uart_ping.py /dev/ttyUSB0
  python scripts/uart_ping.py COM3
  python scripts/uart_ping.py /dev/ttyUSB0 --baud 115200 --count 10
"""

import argparse
import random
import sys
import time

import serial


def ping(port: str, baud: int, count: int, timeout: float) -> None:
    print(f"Открываю {port} @ {baud} бод...")
    try:
        ser = serial.Serial(port, baud, timeout=timeout)
    except serial.SerialException as e:
        print(f"Ошибка открытия порта: {e}")
        sys.exit(1)

    time.sleep(0.1)  # дать время буферу очиститься
    ser.reset_input_buffer()

    ok = 0
    for i in range(1, count + 1):
        value = random.randint(1, 255)
        ser.write(bytes([value]))
        ser.flush()

        response = ser.read(1)
        if response and response[0] == value:
            print(f"  [{i}/{count}] отправил {value:3d} → получил {response[0]:3d}  OK")
            ok += 1
        elif response:
            print(f"  [{i}/{count}] отправил {value:3d} → получил {response[0]:3d}  MISMATCH")
        else:
            print(f"  [{i}/{count}] отправил {value:3d} → таймаут ({timeout}с)")

        time.sleep(0.05)

    ser.close()
    print(f"\nРезультат: {ok}/{count} успешно")
    if ok == count:
        print("Конвертер работает.")
    elif ok == 0:
        print("Конвертер не отвечает — скорее всего неисправен или неверный порт.")
    else:
        print("Частичный ответ — возможны помехи или проблемы с соединением.")


def main():
    parser = argparse.ArgumentParser(description="Ping-тест USB-UART конвертера через Leonardo")
    parser.add_argument("port", help="COM-порт конвертера (например /dev/ttyUSB0 или COM3)")
    parser.add_argument("--baud", type=int, default=9600, help="Скорость (по умолчанию 9600)")
    parser.add_argument("--count", type=int, default=5, help="Количество пингов (по умолчанию 5)")
    parser.add_argument("--timeout", type=float, default=1.0, help="Таймаут ответа в секундах (по умолчанию 1.0)")
    args = parser.parse_args()

    ping(args.port, args.baud, args.count, args.timeout)


if __name__ == "__main__":
    main()
