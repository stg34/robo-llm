"""
HUD Block: информационный блок с заголовком и строками данных.
Поддерживает кириллицу через PIL.
"""
import cv2
import numpy as np

from pilot.widgets._text import get_text_size, put_text

# --- Цвета (BGR, фосфорный зелёный) ---
COLOR_BORDER   = (0, 210, 60)
COLOR_BG       = (0, 18,   5)
COLOR_TITLE_BG = (0, 35,  10)
COLOR_TITLE    = (0, 230, 70)
COLOR_LABEL    = (0, 140, 45)
COLOR_VALUE    = (180, 255, 195)
COLOR_DIVIDER  = (0, 90,  28)

ALPHA_BG = 0.50
CHAMFER  = 10

FONT_SIZE_TITLE = 14   # px при scale=1
FONT_SIZE_ROW   = 12


def _chamfer_poly(x, y, x2, y2, c):
    return np.array([
        [x + c, y],   [x2 - c, y],
        [x2, y + c],  [x2, y2 - c],
        [x2 - c, y2], [x + c, y2],
        [x, y2 - c],  [x, y + c],
    ], dtype=np.int32)


def draw_hud_block(frame: np.ndarray, x: int, y: int,
                   title: str, rows: list[tuple[str, str]],
                   width: int = 260, glow_radius: int = 9,
                   glow_intensity: float = 0.85,
                   scale: float = 1.0) -> np.ndarray:
    """
    Рисует HUD-блок с glow-эффектом поверх frame.

    x, y  — верхний левый угол (в пикселях кадра, уже с учётом scale).
    width — ширина блока при scale=1.
    scale — масштаб относительно эталонного разрешения 896px.
    """
    pad      = int(10 * scale)
    title_h  = int(28 * scale)
    row_h    = int(22 * scale)
    c        = max(4, int(CHAMFER * scale))
    tick     = max(2, int(5  * scale))
    thick    = max(1, int(scale))
    w_scaled = int(width * scale)

    font_title = max(10, int(FONT_SIZE_TITLE * scale))
    font_row   = max(8,  int(FONT_SIZE_ROW   * scale))

    total_h  = title_h + 6 + len(rows) * row_h + pad + 4
    x2       = x + w_scaled
    y2       = y + total_h
    title_y2 = y + title_h + 2

    poly = _chamfer_poly(x, y, x2, y2, c)

    # --- 1. Полупрозрачный фон ---
    overlay = frame.copy()
    cv2.fillPoly(overlay, [poly], COLOR_BG)
    cv2.addWeighted(overlay, ALPHA_BG, frame, 1 - ALPHA_BG, 0, frame)

    # --- 2. Фон заголовка ---
    title_poly = _chamfer_poly(x, y, x2, title_y2, c)
    overlay2 = frame.copy()
    cv2.fillPoly(overlay2, [title_poly], COLOR_TITLE_BG)
    cv2.addWeighted(overlay2, 0.75, frame, 0.25, 0, frame)

    # --- 3. Glow (только геометрия — рамка, засечки, разделитель) ---
    r = max(3, int(glow_radius * scale))
    r = r if r % 2 == 1 else r + 1

    H_f, W_f = frame.shape[:2]
    rx1, ry1 = max(0, x - r),  max(0, y - r)
    rx2, ry2 = min(W_f, x2 + r), min(H_f, y2 + r)
    ox, oy   = rx1, ry1

    glow_roi = np.zeros((ry2 - ry1, rx2 - rx1, 3), dtype=np.uint8)
    poly_r   = poly - np.array([[ox, oy]], dtype=np.int32)
    cv2.polylines(glow_roi, [poly_r], isClosed=True,
                  color=COLOR_BORDER, thickness=thick, lineType=cv2.LINE_AA)
    for px, py, dx, dy in [
        (x + c, y,    1,  0), (x,   y + c,   0,  1),
        (x2 - c, y,  -1,  0), (x2,  y + c,   0,  1),
        (x,  y2 - c,  0, -1), (x + c, y2,    1,  0),
        (x2, y2 - c,  0, -1), (x2 - c, y2,  -1,  0),
    ]:
        cv2.line(glow_roi,
                 (px - ox, py - oy),
                 (px - ox + dx * tick, py - oy + dy * tick),
                 COLOR_BORDER, thick, cv2.LINE_AA)
    cv2.line(glow_roi, (x + c - ox, title_y2 - oy), (x2 - c - ox, title_y2 - oy),
             COLOR_DIVIDER, thick)

    blurred = cv2.GaussianBlur(glow_roi, (r, r), 0)
    blurred = cv2.GaussianBlur(blurred, (r, r), 0)
    frame_roi = frame[ry1:ry2, rx1:rx2]
    frame_roi[:] = np.clip(
        frame_roi.astype(np.float32) + blurred.astype(np.float32) * glow_intensity,
        0, 255,
    ).astype(np.uint8)

    # --- 4. Резкая геометрия ---
    cv2.polylines(frame, [poly], isClosed=True,
                  color=COLOR_BORDER, thickness=thick, lineType=cv2.LINE_AA)
    for px, py, dx, dy in [
        (x + c, y,    1,  0), (x,   y + c,   0,  1),
        (x2 - c, y,  -1,  0), (x2,  y + c,   0,  1),
        (x,  y2 - c,  0, -1), (x + c, y2,    1,  0),
        (x2, y2 - c,  0, -1), (x2 - c, y2,  -1,  0),
    ]:
        cv2.line(frame, (px, py), (px + dx * tick, py + dy * tick),
                 COLOR_BORDER, thick, cv2.LINE_AA)
    cv2.line(frame, (x + c, title_y2), (x2 - c, title_y2), COLOR_DIVIDER, thick)

    # --- 5. Текст через PIL (поддержка кириллицы) ---
    # Заголовок
    _, th_title = get_text_size(title, font_title)
    ty_title = title_y2 - int(6 * scale)   # нижний край = чуть выше разделителя
    put_text(frame, title, (x + pad, ty_title), font_title, COLOR_TITLE)

    # Строки
    for i, (label, value) in enumerate(rows):
        row_y = title_y2 + pad + i * row_h + row_h - int(4 * scale)
        put_text(frame, label + ":", (x + pad, row_y), font_row, COLOR_LABEL)
        vw, _ = get_text_size(value, font_row)
        put_text(frame, value, (x2 - vw - pad, row_y), font_row, COLOR_VALUE)

    return frame
