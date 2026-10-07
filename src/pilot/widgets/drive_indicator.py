"""
Drive Indicator: вертикальный индикатор привода колеса.

Показывает скорость (полоса вверх/вниз от нуля) и ток мотора.
"""
import cv2
import numpy as np

from pilot.widgets._text import get_text_size, put_text

# --- Цвета (BGR) ---
COLOR_BORDER  = (0, 210, 60)
COLOR_BG      = (0, 18,   5)
COLOR_FWD     = (0, 220, 65)    # вперёд
COLOR_REV     = (0, 140, 200)   # назад (голубоватый)
COLOR_STOP    = (0, 180, 50)    # нулевая линия
COLOR_LABEL   = (0, 140, 45)
COLOR_VALUE   = (180, 255, 195)
COLOR_CURRENT = (0, 160, 55)
COLOR_DIM     = (0, 60,  18)    # незаполненная зона

ALPHA_BG = 0.65
CHAMFER  = 5

# Максимальный ток датчика (при raw ADC = 1023)
ADC_MAX_AMPS = 5.0

FONT_SIZE_LABEL = 11   # px при scale=1
FONT_SIZE_VAL   = 14
FONT_SIZE_SMALL = 10


def _chamfer_poly(x, y, x2, y2, c):
    return np.array([
        [x + c, y],   [x2 - c, y],
        [x2, y + c],  [x2, y2 - c],
        [x2 - c, y2], [x + c, y2],
        [x, y2 - c],  [x, y + c],
    ], dtype=np.int32)


def draw_drive_indicator(frame: np.ndarray,
                         x: int, y: int,
                         label: str,
                         speed: int,
                         current_adc: int,
                         max_speed: int = 127,
                         width: int = 45,
                         bar_height: int = 130,
                         scale: float = 1.0) -> np.ndarray:
    """
    Вертикальный индикатор привода колеса.

    Параметры:
        frame       — кадр (BGR numpy array)
        x, y        — верхний левый угол
        label       — подпись ("Л" / "П" или "LEFT" / "RIGHT")
        speed       — скорость -max_speed..+max_speed (положительная = вперёд)
        current_adc — ток, сырой АЦП 0-1023
        max_speed   — максимум шкалы
        width       — ширина виджета при scale=1
        bar_height  — высота шкалы при scale=1
        scale       — масштаб относительно 896px
    """
    pad      = max(2, int(6  * scale))
    label_h  = int(18 * scale)
    fwd_h    = int(14 * scale)
    rev_h    = int(14 * scale)
    info_h   = int(38 * scale)
    c        = max(2, int(CHAMFER * scale))
    thick    = max(1, int(scale))
    w_s      = int(width      * scale)
    bar_h_s  = int(bar_height * scale)

    font_label = max(8,  int(FONT_SIZE_LABEL * scale))
    font_val   = max(10, int(FONT_SIZE_VAL   * scale))
    font_small = max(7,  int(FONT_SIZE_SMALL * scale))

    total_h = label_h + fwd_h + bar_h_s + rev_h + info_h + pad
    x2  = x + w_s
    y2  = y + total_h
    poly = _chamfer_poly(x, y, x2, y2, c)

    # --- 1. Фон ---
    overlay = frame.copy()
    cv2.fillPoly(overlay, [poly], COLOR_BG)
    cv2.addWeighted(overlay, ALPHA_BG, frame, 1 - ALPHA_BG, 0, frame)

    # --- 2. Рамка ---
    cv2.polylines(frame, [poly], isClosed=True,
                  color=COLOR_BORDER, thickness=thick, lineType=cv2.LINE_AA)

    # --- 3. Подпись (PIL) ---
    title_y2 = y + label_h + 2
    lw, _ = get_text_size(label, font_label)
    put_text(frame, label, (x + (w_s - lw) // 2, y + label_h - int(2 * scale)),
             font_label, COLOR_BORDER)
    cv2.line(frame, (x + c, title_y2), (x2 - c, title_y2), COLOR_DIM, thick)

    # --- 4. Шкала ---
    bar_x1  = x + pad
    bar_x2  = x2 - pad
    bar_top = title_y2 + fwd_h
    bar_bot = bar_top + bar_h_s
    bar_mid = (bar_top + bar_bot) // 2

    # Метки FWD / REV
    for text, ty in [("FWD", title_y2 + fwd_h - int(3 * scale)),
                     ("REV", bar_bot + rev_h - int(2 * scale))]:
        tw, _ = get_text_size(text, font_small)
        put_text(frame, text, (x + (w_s - tw) // 2, ty), font_small, COLOR_DIM)

    cv2.rectangle(frame, (bar_x1, bar_top), (bar_x2, bar_bot), COLOR_DIM, -1)

    ratio   = max(-1.0, min(1.0, speed / max_speed))
    fill_px = int(abs(ratio) * (bar_h_s // 2))

    glow_rect = None
    if ratio > 0:
        fy1, fy2   = bar_mid - fill_px, bar_mid
        color_fill = COLOR_FWD
    elif ratio < 0:
        fy1, fy2   = bar_mid, bar_mid + fill_px
        color_fill = COLOR_REV
    else:
        fy1 = fy2  = bar_mid
        color_fill = COLOR_STOP

    if fill_px > 0:
        cv2.rectangle(frame, (bar_x1, fy1), (bar_x2, fy2), color_fill, -1)
        glow_rect = (bar_x1, fy1, bar_x2, fy2, color_fill)

    cv2.line(frame, (bar_x1, bar_mid), (bar_x2, bar_mid), COLOR_STOP, thick + 1)
    for sy in [bar_top, bar_mid, bar_bot]:
        cv2.line(frame, (x,       sy), (x + pad - 1,   sy), COLOR_BORDER, thick)
        cv2.line(frame, (x2 - pad + 1, sy), (x2, sy),       COLOR_BORDER, thick)

    # --- 5. Текстовые значения ---
    info_y   = bar_bot + rev_h
    spd_str  = f"{speed:+d}"
    cur_amps = current_adc / 1023 * ADC_MAX_AMPS
    cur_str  = f"{cur_amps:.1f}A"

    sw, sh = get_text_size(spd_str, font_val)
    cw, _  = get_text_size(cur_str, font_small)
    put_text(frame, spd_str, (x + (w_s - sw) // 2, info_y + sh + int(2 * scale)),
             font_val, COLOR_VALUE)
    put_text(frame, cur_str, (x + (w_s - cw) // 2, info_y + sh + int(2 * scale) + int(16 * scale)),
             font_small, COLOR_CURRENT)

    # --- 6. Glow ---
    glow_r = max(3, int(11 * scale))
    glow_r = glow_r if glow_r % 2 == 1 else glow_r + 1

    H_f, W_f = frame.shape[:2]
    rx1, ry1 = max(0, x - glow_r),  max(0, y - glow_r)
    rx2, ry2 = min(W_f, x2 + glow_r), min(H_f, y2 + glow_r)
    ox, oy   = rx1, ry1

    glow_roi = np.zeros((ry2 - ry1, rx2 - rx1, 3), dtype=np.uint8)
    if glow_rect:
        bx1, by1, bx2, by2, bcol = glow_rect
        cv2.rectangle(glow_roi, (bx1 - ox, by1 - oy), (bx2 - ox, by2 - oy), bcol, -1)
    cv2.line(glow_roi, (bar_x1 - ox, bar_mid - oy), (bar_x2 - ox, bar_mid - oy),
             COLOR_STOP, thick + 1)

    blurred = cv2.GaussianBlur(glow_roi, (glow_r, glow_r), 0)
    blurred = cv2.GaussianBlur(blurred, (glow_r, glow_r), 0)
    frame_roi = frame[ry1:ry2, rx1:rx2]
    frame_roi[:] = np.clip(
        frame_roi.astype(np.float32) + blurred.astype(np.float32) * 0.85,
        0, 255,
    ).astype(np.uint8)

    return frame
