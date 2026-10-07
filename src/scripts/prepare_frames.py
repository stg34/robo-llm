"""
Приведение кадров к единому виду для экспериментов со зрением.

Масштабирует и пережимает jpg тем же кодом, что использует робот
(pilot/camera.py): INTER_AREA до 896 px по ширине, JPEG quality 85.
Одинаковое сжатие для всех кадров обязательно — иначе разница в качестве
станет неконтролируемой переменной эксперимента.

Запуск:
    python3 scripts/prepare_frames.py
    python3 scripts/prepare_frames.py --src DIR --dst DIR --width 896 --quality 85
    python3 scripts/prepare_frames.py --dry-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2

from pilot.camera import CameraSource, _scale_to_width

DEFAULT_SRC = "docs/vision-tools-analysis/big-images"
DEFAULT_DST = "docs/vision-tools-analysis"

parser = argparse.ArgumentParser(description="Пережать кадры к единому размеру и качеству")
parser.add_argument("--src", default=DEFAULT_SRC, help=f"откуда брать (по умолчанию {DEFAULT_SRC})")
parser.add_argument("--dst", default=DEFAULT_DST, help=f"куда класть (по умолчанию {DEFAULT_DST})")
parser.add_argument("--width", type=int, default=896, help="ширина в пикселях (по умолчанию 896)")
parser.add_argument("--quality", type=int, default=85, help="качество JPEG (по умолчанию 85)")
parser.add_argument("--dry-run", action="store_true", help="показать, что будет сделано, но не писать")
args = parser.parse_args()

if not os.path.isdir(args.src):
    print(f"Ошибка: нет каталога {args.src}")
    sys.exit(1)

names = sorted(f for f in os.listdir(args.src) if f.lower().endswith((".jpg", ".jpeg")))
if not names:
    print(f"В {args.src} нет jpg-файлов")
    sys.exit(1)

os.makedirs(args.dst, exist_ok=True)

print(f"{args.src} -> {args.dst}, ширина {args.width}, качество {args.quality}")
print()
print(f"{'файл':24} {'было':>18}  {'стало':>18}")
print("-" * 66)

errors = 0
for name in names:
    src_path = os.path.join(args.src, name)
    dst_path = os.path.join(args.dst, name)

    frame = cv2.imread(src_path)
    if frame is None:
        print(f"{name:24} не читается, пропущен")
        errors += 1
        continue

    h, w = frame.shape[:2]
    src_kb = os.path.getsize(src_path) / 1024

    scaled = _scale_to_width(frame, args.width)
    jpeg = CameraSource.encode_jpeg(scaled, quality=args.quality)
    sh, sw = scaled.shape[:2]

    if not args.dry_run:
        with open(dst_path, "wb") as f:
            f.write(jpeg)

    was = f"{w}x{h} {src_kb:.0f} КБ"
    now = f"{sw}x{sh} {len(jpeg) / 1024:.0f} КБ"
    print(f"{name:24} {was:>18}  {now:>18}")

print()
if args.dry_run:
    print(f"Пробный прогон, ничего не записано. Файлов: {len(names) - errors}")
else:
    print(f"Готово, записано файлов: {len(names) - errors}")
if errors:
    print(f"Пропущено из-за ошибок чтения: {errors}")
