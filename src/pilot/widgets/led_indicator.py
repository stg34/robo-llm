"""
LED Indicator: состояние декоративной подсветки (красный + синий).
"""
import cv2
import numpy as np

from pilot.widgets._text import get_text_size, put_text

COLOR_BORDER = (0, 210, 60)
COLOR_BG     = (0, 18,   5)
COLOR_DIM    = (0, 60,  18)
ALPHA_BG     = 0.65
CHAMFER      = 5

COLOR_RED_ON   = (0,   0, 210)
COLOR_RED_OFF  = (0,   0,  50)
COLOR_BLUE_ON  = (210, 50,  20)
COLOR_BLUE_OFF = (50,  12,   5)

FONT_TITLE = 10
FONT_LABEL = 9


def _chamfer_poly(x, y, x2, y2, c):
    return np.array([
        [x + c, y],   [x2 - c, y],
        [x2, y + c],  [x2, y2 - c],
        [x2 - c, y2], [x + c, y2],
        [x, y2 - c],  [x, y + c],
    ], dtype=np.int32)


def draw_led_indicator(
    frame: np.ndarray,
    x: int, y: int,
    red: bool,
    blue: bool,
    scale: float = 1.0,
) -> np.ndarray:
    """Виджет состояния декоративной подсветки."""
    w_s    = int(80  * scale)
    h_s    = int(62  * scale)
    c      = max(2, int(CHAMFER * scale))
    thick  = max(1, int(scale))
    pad    = max(2, int(6 * scale))
    x2, y2 = x + w_s, y + h_s
    poly   = _chamfer_poly(x, y, x2, y2, c)

    # Фон
    overlay = frame.copy()
    cv2.fillPoly(overlay, [poly], COLOR_BG)
    cv2.addWeighted(overlay, ALPHA_BG, frame, 1 - ALPHA_BG, 0, frame)

    # Рамка
    cv2.polylines(frame, [poly], isClosed=True,
                  color=COLOR_BORDER, thickness=thick, lineType=cv2.LINE_AA)

    # Заголовок
    title_font = max(8, int(FONT_TITLE * scale))
    title = "LIGHTS"
    tw, th = get_text_size(title, title_font)
    title_y = y + pad + th
    put_text(frame, title, (x + (w_s - tw) // 2, title_y), title_font, COLOR_BORDER)

    sep_y = title_y + pad
    cv2.line(frame, (x + c, sep_y), (x2 - c, sep_y), COLOR_DIM, thick)

    # Кружки
    r_circle = max(8, int(13 * scale))
    circle_y = sep_y + pad + r_circle + max(1, int(3 * scale))
    left_cx  = x + w_s // 4
    right_cx = x + 3 * w_s // 4

    for cx, color_on, color_off, is_on in [
        (left_cx,  COLOR_RED_ON,  COLOR_RED_OFF,  red),
        (right_cx, COLOR_BLUE_ON, COLOR_BLUE_OFF, blue),
    ]:
        color = color_on if is_on else color_off
        cv2.circle(frame, (cx, circle_y), r_circle, color, -1, lineType=cv2.LINE_AA)
        cv2.circle(frame, (cx, circle_y), r_circle, COLOR_BORDER if is_on else COLOR_DIM,
                   thick, lineType=cv2.LINE_AA)

    # Подписи
    label_font = max(7, int(FONT_LABEL * scale))
    label_y    = circle_y + r_circle + max(3, int(5 * scale))
    for cx, letter, is_on in [(left_cx, "R", red), (right_cx, "B", blue)]:
        lw, _ = get_text_size(letter, label_font)
        col   = COLOR_BORDER if is_on else COLOR_DIM
        put_text(frame, letter, (cx - lw // 2, label_y), label_font, col)

    # Glow для включённых диодов
    glow_r = max(3, int(13 * scale))
    glow_r = glow_r if glow_r % 2 == 1 else glow_r + 1
    H_f, W_f = frame.shape[:2]
    rx1 = max(0, x - glow_r);  ry1 = max(0, y - glow_r)
    rx2 = min(W_f, x2 + glow_r); ry2 = min(H_f, y2 + glow_r)

    glow_layer = np.zeros((ry2 - ry1, rx2 - rx1, 3), dtype=np.uint8)
    for cx, color_on, is_on in [
        (left_cx,  COLOR_RED_ON,  red),
        (right_cx, COLOR_BLUE_ON, blue),
    ]:
        if is_on:
            cv2.circle(glow_layer, (cx - rx1, circle_y - ry1),
                       r_circle, color_on, -1, lineType=cv2.LINE_AA)

    if red or blue:
        blurred = cv2.GaussianBlur(glow_layer, (glow_r, glow_r), 0)
        blurred = cv2.GaussianBlur(blurred,    (glow_r, glow_r), 0)
        roi = frame[ry1:ry2, rx1:rx2]
        roi[:] = np.clip(
            roi.astype(np.float32) + blurred.astype(np.float32) * 0.9,
            0, 255,
        ).astype(np.uint8)

    return frame
