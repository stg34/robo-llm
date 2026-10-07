"""
HUD-наложение: субтитры, метки действий, виджеты телеметрии.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.config import HUD_SHOW_TOKENS, HUD_BLOCK_X, HUD_BLOCK_Y
from pilot.widgets.drive_indicator import draw_drive_indicator
from pilot.widgets.hud_block import (draw_hud_block, _chamfer_poly,
                                      COLOR_BORDER, COLOR_BG, COLOR_TITLE_BG,
                                      COLOR_TITLE, COLOR_VALUE, COLOR_DIVIDER,
                                      ALPHA_BG, CHAMFER)
from pilot.widgets.rangefinder import draw_rangefinder
from pilot.widgets.angle_ruler import draw_angle_ruler
from pilot.widgets._text import put_text, get_text_size

# ─── Цвета ───────────────────────────────────────────────────────────────────

COLOR_THINKING = (0,  180,  255)   # оранжевый: Claude думает
COLOR_SPEAKING = (0,  220,   65)   # зелёный: речь
COLOR_MOVING   = (200, 220,   0)   # жёлтый: движение
COLOR_DONE     = (180, 180, 180)   # серый: завершено

ACTION_LABELS = {
    "thinking": ("ДУМАЕТ",    COLOR_THINKING),
    "speaking": ("ГОВОРИТ",   COLOR_SPEAKING),
    "move":     ("ЕДЕТ",      COLOR_MOVING),
    "turn":     ("ПОВОРОТ",   COLOR_MOVING),
    "stop":     ("СТОП",      COLOR_SPEAKING),
    "done":     ("ГОТОВО",    COLOR_DONE),
    "idle":     ("",          COLOR_DONE),
}


def draw_subtitle(frame: np.ndarray, text: str, scale: float):
    """Субтитры в нижней части экрана."""
    if not text:
        return
    max_chars = 80
    lines = []
    words = text.split()
    line = ""
    for w in words:
        if len(line) + len(w) + 1 > max_chars:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        lines.append(line)

    H, W = frame.shape[:2]
    font_size = max(12, int(18 * scale))
    line_h = int(font_size * 1.4)
    pad = int(8 * scale)
    y_base = H - int(20 * scale) - line_h * (len(lines) - 1)

    for i, ln in enumerate(lines):
        tw, th = get_text_size(ln, font_size)
        x = (W - tw) // 2
        y = y_base + i * line_h
        # Полупрозрачная подложка
        roi = frame[y - th - pad: y + pad, x - pad: x + tw + pad]
        if roi.size > 0:
            overlay = roi.copy()
            overlay[:] = (0, 0, 0)
            cv2.addWeighted(overlay, 0.55, roi, 0.45, 0, roi)
        put_text(frame, ln, (x, y), font_size, (220, 220, 220))


def draw_action_label(frame: np.ndarray, action: str, scale: float):
    """Метка текущего действия в правом верхнем углу."""
    label, color = ACTION_LABELS.get(action, ("", COLOR_DONE))
    if not label:
        return
    H, W = frame.shape[:2]
    font_size = max(14, int(20 * scale))
    tw, th = get_text_size(label, font_size)
    pad = int(10 * scale)
    x = W - tw - int(32 * scale)
    y = int(32 * scale) + th
    roi = frame[y - th - pad: y + pad, x - pad: x + tw + pad]
    if roi.size > 0:
        overlay = roi.copy()
        overlay[:] = (0, 0, 0)
        cv2.addWeighted(overlay, 0.6, roi, 0.4, 0, roi)
    put_text(frame, label, (x, y), font_size, color)


def draw_operator_message(frame: np.ndarray, text: str, title: str, scale: float):
    """Центрированный HUD-блок с ответом оператора (-i режим)."""
    H, W = frame.shape[:2]

    font_title = max(10, int(14 * scale))
    font_text  = max(8,  int(13 * scale))
    pad        = int(10 * scale)
    title_h    = int(28 * scale)
    row_h      = int(22 * scale)
    block_w    = int(420 * scale)
    max_text_w = block_w - 2 * pad

    # Перенос по пробелам, ориентируясь на реальную ширину текста
    words = text.split()
    lines: list[str] = []
    line = ""
    for w in words:
        candidate = (line + " " + w).strip()
        tw, _ = get_text_size(candidate, font_text)
        if tw > max_text_w and line:
            lines.append(line)
            line = w
        else:
            line = candidate
    if line:
        lines.append(line)

    total_h  = title_h + 6 + len(lines) * row_h + pad + 4
    x        = (W - block_w) // 2
    y        = (H - total_h) // 2
    x2       = x + block_w
    y2       = y + total_h
    title_y2 = y + title_h + 2
    c        = max(4, int(CHAMFER * scale))
    thick    = max(1, int(scale))
    tick     = max(2, int(5 * scale))

    poly       = _chamfer_poly(x, y, x2, y2, c)
    title_poly = _chamfer_poly(x, y, x2, title_y2, c)

    # Фон
    ov = frame.copy()
    cv2.fillPoly(ov, [poly], COLOR_BG)
    cv2.addWeighted(ov, ALPHA_BG, frame, 1 - ALPHA_BG, 0, frame)

    # Фон заголовка
    ov2 = frame.copy()
    cv2.fillPoly(ov2, [title_poly], COLOR_TITLE_BG)
    cv2.addWeighted(ov2, 0.75, frame, 0.25, 0, frame)

    # Glow
    corners = [
        (x + c, y,    1,  0), (x,    y + c,   0,  1),
        (x2 - c, y,  -1,  0), (x2,   y + c,   0,  1),
        (x,  y2 - c,  0, -1), (x + c, y2,     1,  0),
        (x2, y2 - c,  0, -1), (x2 - c, y2,   -1,  0),
    ]
    glow = np.zeros_like(frame)
    cv2.polylines(glow, [poly], isClosed=True, color=COLOR_BORDER, thickness=thick, lineType=cv2.LINE_AA)
    for px, py, dx, dy in corners:
        cv2.line(glow, (px, py), (px + dx * tick, py + dy * tick), COLOR_BORDER, thick, cv2.LINE_AA)
    cv2.line(glow, (x + c, title_y2), (x2 - c, title_y2), COLOR_DIVIDER, thick)
    r = max(3, int(9 * scale))
    r = r if r % 2 == 1 else r + 1
    blurred = cv2.GaussianBlur(glow, (r, r), 0)
    blurred = cv2.GaussianBlur(blurred, (r, r), 0)
    frame[:] = np.clip(frame.astype(np.float32) + blurred.astype(np.float32) * 0.85, 0, 255).astype(np.uint8)

    # Резкая геометрия
    cv2.polylines(frame, [poly], isClosed=True, color=COLOR_BORDER, thickness=thick, lineType=cv2.LINE_AA)
    for px, py, dx, dy in corners:
        cv2.line(frame, (px, py), (px + dx * tick, py + dy * tick), COLOR_BORDER, thick, cv2.LINE_AA)
    cv2.line(frame, (x + c, title_y2), (x2 - c, title_y2), COLOR_DIVIDER, thick)

    # Текст
    put_text(frame, title, (x + pad, title_y2 - int(6 * scale)), font_title, COLOR_TITLE)
    for i, ln in enumerate(lines):
        row_y = title_y2 + pad + i * row_h + row_h - int(4 * scale)
        put_text(frame, ln, (x + pad, row_y), font_text, COLOR_VALUE)


def apply_hud(frame: np.ndarray, t: dict | None, action: str, subtitle: str,
              turn_n: int, scale: float, tokens: int | None = None,
              operator_overlay: tuple[str, str] | None = None):
    """Наложить все виджеты HUD на кадр."""
    range_mm = t["range_mm"] if t else 0
    voltage  = t["voltage"]  if t else 0.0
    l_speed  = t["left_speed"]   if t else 0
    r_speed  = t["right_speed"]  if t else 0
    l_curr   = t["left_current"] if t else 0
    r_curr   = t["right_current"] if t else 0
    heading_str = f"{t['gyro_angle']:.0f} deg" if t and "gyro_angle" in t else "—"

    H, W = frame.shape[:2]

    draw_angle_ruler(frame, scale=scale)
    draw_rangefinder(frame, range_mm, scale=scale)

    rows = [
        ("Voltage", f"{voltage:.1f} V"),
        ("Heading", heading_str),
        ("Range",   f"{range_mm} mm"),
        ("Turn",    str(turn_n)),
    ]
    if HUD_SHOW_TOKENS:
        if tokens is None:
            rows.append(("Tokens", "—"))
        else:
            rows.append(("Tokens", f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)))

    draw_hud_block(frame,
                   x=int(HUD_BLOCK_X * scale), y=int(HUD_BLOCK_Y * scale),
                   title="УПИЗДЕНЬ v. 0.1.1",
                   rows=rows,
                   scale=scale)

    ind_w  = int(45 * scale)
    margin = int(HUD_BLOCK_Y * scale)
    ind_y  = H - int(246 * scale)
    draw_drive_indicator(frame, x=margin, y=ind_y,
                         label="L", speed=l_speed, current_adc=l_curr, scale=scale)
    draw_drive_indicator(frame, x=W - margin - ind_w, y=ind_y,
                         label="R", speed=r_speed, current_adc=r_curr, scale=scale)

    if operator_overlay:
        draw_operator_message(frame, operator_overlay[0], operator_overlay[1], scale)

    draw_subtitle(frame, subtitle, scale)
    draw_action_label(frame, action, scale)
