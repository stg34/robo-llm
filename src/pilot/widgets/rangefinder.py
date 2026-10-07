"""
Rangefinder HUD: индикатор лазерного дальномера.

Рисует прицельную сетку в центре кадра с расстоянием до препятствия.
"""
import math

import cv2
import numpy as np

from pilot.widgets._text import get_text_size, put_text

# --- Цвета (BGR, фосфорный зелёный) ---
COLOR_GRID   = (0, 200, 55)
COLOR_CENTER = (0, 255, 120)
COLOR_CIRCLE = (0, 210, 60)
COLOR_VALUE  = (180, 255, 195)
COLOR_UNIT   = (0, 140, 45)

ALPHA_BG = 0.15

FONT_SIZE_VAL  = 17   # px при scale=1
FONT_SIZE_UNIT = 12


def _chord_y(cy, R, dx):
    d2 = R * R - dx * dx
    if d2 <= 0:
        return None, None
    h = math.sqrt(d2)
    return int(cy - h), int(cy + h)


def _chord_x(cx, R, dy):
    d2 = R * R - dy * dy
    if d2 <= 0:
        return None, None
    w = math.sqrt(d2)
    return int(cx - w), int(cx + w)


def _radial_gradient(H, W, cx, cy, R, power=1.4):
    Y, X = np.ogrid[:H, :W]
    dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2).astype(np.float32)
    return np.clip(1.0 - (dist / R) ** power, 0.0, 1.0)


def _draw_grid(canvas, cx, cy, R, grid_n, ext):
    step = R / grid_n
    for i in range(-grid_n + 1, grid_n):
        dx = int(i * step)
        y1, y2 = _chord_y(cy, R, dx)
        if y1 is not None:
            cv2.line(canvas, (cx + dx, y1 - ext), (cx + dx, y2 + ext),
                     COLOR_GRID, 1, cv2.LINE_AA)
    for i in range(-grid_n + 1, grid_n):
        dy = int(i * step)
        x1, x2 = _chord_x(cx, R, dy)
        if x1 is not None:
            cv2.line(canvas, (x1 - ext, cy + dy), (x2 + ext, cy + dy),
                     COLOR_GRID, 1, cv2.LINE_AA)


def _draw_decorative(canvas, cx, cy, R, dot_r):
    cv2.circle(canvas, (cx, cy), R,       COLOR_CIRCLE, 1, cv2.LINE_AA)
    cv2.circle(canvas, (cx, cy), dot_r,   COLOR_CENTER, -1, cv2.LINE_AA)
    cv2.circle(canvas, (cx, cy), dot_r+1, COLOR_CIRCLE,  1, cv2.LINE_AA)


def draw_rangefinder(frame: np.ndarray, range_mm: int,
                     cx: int | None = None, cy: int | None = None,
                     radius: int = 70, grid_n: int = 3, ext: int = 8,
                     glow_intensity: float = 0.9,
                     scale: float = 1.0) -> np.ndarray:
    """
    Рисует индикатор лазерного дальномера поверх frame.

    range_mm — расстояние в мм (0 = нет данных).
    scale    — масштаб относительно эталонного разрешения 896px.
    """
    H, W = frame.shape[:2]
    if cx is None:
        cx = W // 2
    if cy is None:
        cy = H // 2

    R      = int(radius * scale)
    dot_r  = max(2, int(4 * scale))
    pad    = int(12 * scale)
    ext_s  = max(1, int(ext * scale))

    # --- 1. Полупрозрачный фон ---
    overlay = frame.copy()
    cv2.circle(overlay, (cx, cy), R + ext_s + 2, (0, 18, 5), -1)
    cv2.addWeighted(overlay, ALPHA_BG, frame, 1 - ALPHA_BG, 0, frame)

    # --- 2 + 3. Сетка и glow в ROI вокруг прицела ---
    glow_r = max(1, int(13 * scale))
    glow_r = glow_r if glow_r % 2 == 1 else glow_r + 1
    roi_m  = glow_r + ext_s + 2

    rx1, ry1 = max(0, cx - R - roi_m), max(0, cy - R - roi_m)
    rx2, ry2 = min(W, cx + R + roi_m), min(H, cy + R + roi_m)
    rH, rW   = ry2 - ry1, rx2 - rx1
    cx_r, cy_r = cx - rx1, cy - ry1

    if rH <= 0 or rW <= 0:
        # Центр прицела за пределами кадра — рисуем только декор и метку
        _draw_decorative(frame, cx, cy, R, dot_r)
        return frame

    grid_roi = np.zeros((rH, rW, 3), dtype=np.float32)
    _draw_grid(grid_roi, cx_r, cy_r, R, grid_n, ext_s)
    mask_roi = _radial_gradient(rH, rW, cx_r, cy_r, R, power=1.4)
    grid_roi *= mask_roi[:, :, np.newaxis]

    blurred = cv2.GaussianBlur(grid_roi.astype(np.uint8), (glow_r, glow_r), 0)
    blurred = cv2.GaussianBlur(blurred, (glow_r, glow_r), 0)
    frame_roi = frame[ry1:ry2, rx1:rx2]
    frame_roi[:] = np.clip(
        frame_roi.astype(np.float32) + blurred.astype(np.float32) * glow_intensity,
        0, 255,
    ).astype(np.uint8)

    # --- 4. Резкие элементы ---
    frame_roi[:] = np.clip(frame_roi.astype(np.float32) + grid_roi, 0, 255).astype(np.uint8)
    _draw_decorative(frame, cx, cy, R, dot_r)

    # --- 5. Подпись расстояния (PIL) ---
    font_val  = max(10, int(FONT_SIZE_VAL  * scale))
    font_unit = max(8,  int(FONT_SIZE_UNIT * scale))

    val_str  = str(range_mm) if range_mm > 0 else "---"
    unit_str = " mm"         if range_mm > 0 else ""

    vw, vh = get_text_size(val_str,  font_val)
    uw, _  = get_text_size(unit_str, font_unit) if unit_str else (0, 0)

    lx = cx + R + pad
    ly = cy + vh // 2   # нижний край текста

    put_text(frame, val_str,  (lx, ly),        font_val,  COLOR_VALUE)
    put_text(frame, unit_str, (lx + vw, ly),   font_unit, COLOR_UNIT)

    return frame
