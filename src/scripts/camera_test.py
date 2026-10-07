"""
Быстрая проверка захвата кадра с Tapo-камеры.

Запуск:
    python3 scripts/camera_test.py

Сохраняет frame.jpg в текущую директорию и выводит размер файла.
Переопределить URL:
    TAPO_HOST=192.168.1.50 TAPO_USER=admin TAPO_PASSWORD=secret python3 scripts/camera_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pilot.camera import CameraSource
from pilot.config import tapo_rtsp_url
from pilot.widgets.angle_ruler import draw_angle_ruler

url = tapo_rtsp_url()
print(f"Подключение к {url} ...")

import time

cam = CameraSource(url)
cam.start()

# Ждём первого кадра (до 5 секунд)
frame_orig = None
for _ in range(50):
    frame_orig, frame = cam.capture_frames()
    if frame is not None:
        break
    time.sleep(0.1)

cam.stop()

if frame is None:
    print("Ошибка: не удалось получить кадр.")
    sys.exit(1)

h, w = frame_orig.shape[:2]
print(f"Кадр получен: {w}x{h} (AI: {frame.shape[1]}x{frame.shape[0]})")

draw_angle_ruler(frame)

jpeg = cam.encode_jpeg(frame_orig)
out = "frame.jpg"
with open(out, "wb") as f:
    f.write(jpeg)

print(f"Сохранено: {out} ({len(jpeg) // 1024} КБ)")
