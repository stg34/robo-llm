"""
Angle ruler: вертикальные линии-ориентиры для оценки угла поворота на кадре.

Калибровка: измерьте реальное смещение объекта на N° и скорректируйте ANGLE_OFFSETS_PX.
Смещение задаётся относительно центра кадра (cx), положительное = правее.
"""
import cv2
import numpy as np

from pilot.widgets._text import get_text_size, put_text

# --- Цвета (BGR, фосфорный зелёный) ---
COLOR_LINE   = (0, 210, 60)
COLOR_CENTER = (0, 255, 80)
COLOR_LABEL  = (0, 255, 80)

# Смещения линий в пикселях от центра кадра (откалибровано при 896px).
ANGLE_OFFSETS_PX: dict[int, int] = {
    -30: -382,
    -20: -258,
    -10: -129,
      0:    0,
     10:  129,
     20:  258,
     30:  382,
}

LINE_ALPHA       = 0.55
LINE_THICKNESS   = 1
CENTER_THICKNESS = 1
GLOW_RADIUS      = 0
GLOW_INTENSITY   = 0.3

MARGIN_TOP    = 30   # место для подписи сверху (px при scale=1)
MARGIN_BOTTOM = 18
FONT_SIZE     = 17   # размер шрифта в пикселях (при scale=1)

PIXEL_FONT_SIZE = 13   # подписи пиксельных координат (меньше градусных)
H_LINE_ALPHA    = 0.55
H_LINE_FRACS    = (0.25, 0.5, 0.75)  # горизонтальные линии на 25/50/75% высоты


def draw_angle_ruler(frame: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """
    Наносит вертикальные линии-ориентиры углов поворота на кадр.

    scale — масштаб относительно эталонного разрешения 896px.
    """
    h, w = frame.shape[:2]
    cx = w // 2

    margin_top    = int(MARGIN_TOP    * scale)
    margin_bottom = int(MARGIN_BOTTOM * scale)
    font_size     = max(10, int(FONT_SIZE * scale))
    line_thick    = max(1, int(LINE_THICKNESS   * scale))
    center_thick  = max(1, int(CENTER_THICKNESS * scale))

    y1 = margin_top
    y2 = h - margin_bottom

    # --- Glow ---
    glow_layer = np.zeros_like(frame)
    for angle, offset in ANGLE_OFFSETS_PX.items():
        x = cx + int(offset * scale)
        if x < 0 or x >= w:
            continue
        color = COLOR_CENTER if angle == 0 else COLOR_LINE
        thick = center_thick if angle == 0 else line_thick
        cv2.line(glow_layer, (x, y1), (x, y2), color, thick, cv2.LINE_AA)

    if GLOW_RADIUS > 0:
        r = GLOW_RADIUS if GLOW_RADIUS % 2 == 1 else GLOW_RADIUS + 1
        blurred = cv2.GaussianBlur(glow_layer, (r, r), 0)
        blurred = cv2.GaussianBlur(blurred, (r, r), 0)
        frame[:] = np.clip(
            frame.astype(np.float32) + blurred.astype(np.float32) * GLOW_INTENSITY,
            0, 255,
        ).astype(np.uint8)

    # --- Резкие линии с прозрачностью ---
    overlay = frame.copy()
    for angle, offset in ANGLE_OFFSETS_PX.items():
        x = cx + int(offset * scale)
        if x < 0 or x >= w:
            continue
        color = COLOR_CENTER if angle == 0 else COLOR_LINE
        thick = center_thick if angle == 0 else line_thick
        cv2.line(overlay, (x, y1), (x, y2), color, thick, cv2.LINE_AA)
    cv2.addWeighted(overlay, LINE_ALPHA, frame, 1.0 - LINE_ALPHA, 0, frame)

    # --- Подписи сверху (PIL — поддержка кириллицы) ---
    pad = max(2, int(3 * scale))
    for angle, offset in ANGLE_OFFSETS_PX.items():
        x = cx + int(offset * scale)
        if x < 0 or x >= w:
            continue

        label = "0°" if angle == 0 else f"{angle:+d}°"
        tw, th = get_text_size(label, font_size)
        tx = x - tw // 2
        ty = y1 - int(4 * scale)   # нижний край текста чуть выше y1

        # полупрозрачная подложка
        rx1, ry1 = tx - pad, ty - th - pad
        rx2, ry2 = tx + tw + pad, ty + pad
        if ry1 >= 0 and rx1 >= 0 and rx2 <= w and ry2 <= h:
            roi = frame[ry1:ry2, rx1:rx2]
            roi[:] = (roi.astype(np.float32) * 0.6).astype(np.uint8)

        put_text(frame, label, (tx, ty), font_size, COLOR_LABEL)

    # # --- Пиксельные X-метки снизу (координаты в пространстве 896px-кадра) ---
    # px_font = max(8, int(PIXEL_FONT_SIZE * scale))
    # for angle, offset in ANGLE_OFFSETS_PX.items():
    #     x = cx + int(offset * scale)
    #     if x < 0 or x >= w:
    #         continue
    #     label_px = f"{448 + offset} px"
    #     tw, th = get_text_size(label_px, px_font)
    #     tx = x - tw // 2
    #     ty = h - pad           # нижний край текста у нижнего края кадра
    #     rx1, ry1 = tx - pad, ty - th - pad
    #     rx2, ry2 = tx + tw + pad, ty + pad
    #     if ry1 >= 0 and rx1 >= 0 and rx2 <= w and ry2 <= h:
    #         roi = frame[ry1:ry2, rx1:rx2]
    #         roi[:] = (roi.astype(np.float32) * 0.6).astype(np.uint8)
    #     put_text(frame, label_px, (tx, ty), px_font, COLOR_LABEL)

    # # --- Горизонтальные линии с Y-метками (координаты в пространстве 896px-кадра) ---
    # h_overlay = frame.copy()
    # for frac in H_LINE_FRACS:
    #     y = int(h * frac)
    #     cv2.line(h_overlay, (0, y), (w, y), COLOR_LINE, 1, cv2.LINE_AA)
    # cv2.addWeighted(h_overlay, H_LINE_ALPHA, frame, 1.0 - H_LINE_ALPHA, 0, frame)
    #
    # for frac in H_LINE_FRACS:
    #     y = int(h * frac)
    #     label_y = f"{int(y / scale)} px"
    #     tw, th = get_text_size(label_y, px_font)
    #     tx = pad + 2
    #     ty = y - 2
    #     rx1, ry1 = tx - pad, ty - th - pad
    #     rx2, ry2 = tx + tw + pad, ty + pad
    #     if ry1 >= 0 and rx1 >= 0 and rx2 <= w and ry2 <= h:
    #         roi = frame[ry1:ry2, rx1:rx2]
    #         roi[:] = (roi.astype(np.float32) * 0.6).astype(np.uint8)
    #     put_text(frame, label_y, (tx, ty), px_font, COLOR_LABEL)

    return frame
