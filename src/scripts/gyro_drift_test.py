"""
Тест дрейфа гироскопа и стабильности компаса в покое.

Мониторит телеметрию N секунд, пока робот стоит неподвижно.
Показывает каждый пакет: timestamp, gyro_z, compass heading.
В конце — статистику: среднее и разброс гироскопа, диапазон компаса.

Запуск:
    python3 scripts/gyro_drift_test.py          # 10 секунд
    python3 scripts/gyro_drift_test.py 5        # 5 секунд
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.serial_link import SerialLink
from pilot.compass import Compass

PORT = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0


def main():
    print(f"Подключение к {PORT}...")
    link = SerialLink(PORT)

    if not link.wait_for_packet(timeout=5.0):
        print("Ошибка: нет телеметрии.")
        link.close()
        sys.exit(1)

    compass = Compass()

    print("Калибровка гироскопа (~3с)...", end=" ", flush=True)
    bias = link.calibrate_gyro(n_packets=30)
    print(f"bias={bias:+.2f}°/s\n")

    t0 = link.get_telemetry()
    heading0 = compass.heading(t0.mag_x, t0.mag_y)
    print(f"Начало. Heading: {heading0:.1f}°  gyro_z (после фильтра): {t0.gyro_z:+.2f}°/s")
    print(f"Мониторинг {DURATION:.0f} секунд...\n")
    print(f"{'t,s':>6}  {'gyro_z,°/s':>12}  {'heading,°':>10}")
    print("-" * 34)

    gyro_samples: list[float] = []
    heading_samples: list[float] = []
    start = time.monotonic()
    last_seq = -1

    try:
        while time.monotonic() - start < DURATION:
            link.wait_for_packet(timeout=0.5)
            t = link.get_telemetry()

            # Skip duplicate packets (same sequence number)
            if t.sq == last_seq:
                continue
            last_seq = t.sq

            h = compass.heading(t.mag_x, t.mag_y)
            elapsed = time.monotonic() - start

            gyro_samples.append(t.gyro_z)
            heading_samples.append(h)

            print(f"{elapsed:6.2f}  {t.gyro_z:+12.2f}  {h:10.2f}")

    except KeyboardInterrupt:
        print("\nПрервано.")
    finally:
        link.close()

    if not gyro_samples:
        return

    # Statistics
    n = len(gyro_samples)
    g_mean = sum(gyro_samples) / n
    g_max = max(gyro_samples)
    g_min = min(gyro_samples)

    h_mean = sum(heading_samples) / n
    h_max = max(heading_samples)
    h_min = min(heading_samples)

    print()
    print("═" * 34)
    print(f"Пакетов:       {n}")
    print(f"Гироскоп:      среднее {g_mean:+.2f}°/s  разброс [{g_min:+.2f} .. {g_max:+.2f}]")
    print(f"Компас:        среднее {h_mean:.1f}°  разброс [{h_min:.1f} .. {h_max:.1f}]  Δ={h_max-h_min:.1f}°")
    print(f"Дрейф за {DURATION:.0f}с:  {g_mean * DURATION:.1f}° (если не компенсировать)")


if __name__ == "__main__":
    main()
