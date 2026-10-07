"""
Тест HUD-виджетов на статичном кадре.

Запуск:
    python3 scripts/hud_test.py [путь_к_файлу]

По умолчанию берёт scripts/frame.jpg.
Сохраняет результат в scripts/frame_hud.jpg.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2

from pilot.config import robot_hud_title, HUD_BLOCK_X, HUD_BLOCK_Y
from pilot.widgets.angle_ruler import draw_angle_ruler
from pilot.widgets.drive_indicator import draw_drive_indicator
from pilot.widgets.hud_block import draw_hud_block
from pilot.widgets.rangefinder import draw_rangefinder
from pilot.widgets.led_indicator import draw_led_indicator

input_path  = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "frame.jpg")
output_path = os.path.splitext(input_path)[0] + "_hud.jpg"

frame = cv2.imread(input_path)
if frame is None:
    print(f"Ошибка: не удалось прочитать {input_path}")
    sys.exit(1)

h, w = frame.shape[:2]
print(f"Кадр: {w}x{h}")

# --- Наложение HUD ---
from pilot.camera import TARGET_WIDTH
scale = w / TARGET_WIDTH
# draw_angle_ruler(frame, scale=scale)
# draw_rangefinder(frame, range_mm=1250, scale=scale, cy=200)
draw_hud_block(frame,
               x=int(HUD_BLOCK_X * scale), y=int(HUD_BLOCK_Y * scale),
               title=robot_hud_title(),
               rows=[
                   ("Voltage", "11.8 V"),
                   ("Heading", "142 deg"),
                   ("Range",   "1250 mm"),
                   ("Turn",    "7"),
               ],
               scale=scale)

# Индикаторы моторов
ind_w = int(45 * scale)
margin = int(16 * scale)
ind_y = h - int(246 * scale)
draw_drive_indicator(frame, x=margin, y=ind_y,
                     label="Л", speed=0, current_adc=0, scale=scale)
draw_drive_indicator(frame, x=w - margin - ind_w, y=ind_y,
                     label="П", speed=-0, current_adc=0, scale=scale)

H, W = frame.shape[:2]

led_w = int(80 * scale)
led_h = int(62 * scale)
led_x = margin + ind_w + int(16 * scale)
led_y = H - led_h - int(27 * scale)
led_red = False
led_blue = True
draw_led_indicator(frame, led_x, led_y, red=led_red, blue=led_blue, scale=scale)

cv2.imwrite(output_path, frame)
print(f"Сохранено: {output_path}")
