"""
Zoom-анимация: прицел → Ken Burns наезд → удержание → возврат.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.camera import TARGET_WIDTH
from pilot.widgets._text import put_text, get_text_size

# ─── Константы ───────────────────────────────────────────────────────────────

ZOOM_COLOR            = (0, 255, 65)   # фосфорный зелёный
ZOOM_ANIM_TARGET_SEC  = 0.4
ZOOM_ANIM_PUSH_SEC    = 0.5
ZOOM_ANIM_HOLD_SEC    = 2.0
ZOOM_ANIM_FADE_SEC    = 0.5


def build_zoom_pause_map(timeline: list[dict], pauses: list[tuple]) -> dict:
    """Вернуть {pause_idx: {cx_ai, cy_ai, zoom_num}} для пауз, следующих за zoom.

    Для каждого zoom tool_end ищем ближайшую паузу, начинающуюся ПОСЛЕ tool_end
    (без жёсткого порога — между zoom и паузой может быть operator_message).
    """
    start_by_turn: dict = {}
    zoom_calls = []
    zoom_num = 0
    for e in timeline:
        if e["type"] == "tool_start" and e.get("tool") == "zoom":
            start_by_turn[e.get("turn")] = e
        elif e["type"] == "tool_end" and e.get("tool") == "zoom":
            zoom_num += 1
            s = start_by_turn.get(e.get("turn"))
            if s:
                zoom_calls.append({
                    "vt_end":   e["vt"],
                    "cx_ai":    s["input"]["cx"],
                    "cy_ai":    s["input"]["cy"],
                    "zoom_num": zoom_num,
                })

    result = {}
    for zc in zoom_calls:
        for i, (ps, _pe) in enumerate(pauses):
            if ps >= zc["vt_end"] and i not in result:
                result[i] = zc
                break
    return result


def _draw_reticle(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                  cx: int, cy: int, alpha: float, scale: float):
    """Фосфорный прицел: рамка + угловые скобки + перекрестие."""
    overlay = frame.copy()
    color  = ZOOM_COLOR
    thick  = max(1, int(2 * scale))
    blen   = int(28 * scale)
    bthick = max(2, int(3 * scale))

    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, thick)

    for bx, by in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
        dx = 1 if bx == x1 else -1
        dy = 1 if by == y1 else -1
        cv2.line(overlay, (bx, by), (bx + dx * blen, by), color, bthick)
        cv2.line(overlay, (bx, by), (bx, by + dy * blen), color, bthick)

    clen = int(14 * scale)
    cv2.line(overlay, (cx - clen, cy), (cx + clen, cy), color, thick)
    cv2.line(overlay, (cx, cy - clen), (cx, cy + clen), color, thick)

    cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0, frame)


def generate_zoom_frames(src_frame: np.ndarray,
                         cx_ai: int, cy_ai: int,
                         zoom_result: np.ndarray,
                         fps: float,
                         W_ai: int, H_ai: int) -> list[np.ndarray]:
    """Ken Burns zoom: прицел → наезд → удержание → возврат."""
    H, W = src_frame.shape[:2]
    scale = W / TARGET_WIDTH

    crop_w = W // 2
    crop_h = H // 2
    cx = int(cx_ai * W / W_ai)
    cy = int(cy_ai * H / H_ai)
    x1 = max(0, cx - crop_w // 2)
    y1 = max(0, cy - crop_h // 2)
    x2, y2 = x1 + crop_w, y1 + crop_h
    if x2 > W: x1, x2 = W - crop_w, W
    if y2 > H: y1, y2 = H - crop_h, H
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2

    n_target = max(1, int(ZOOM_ANIM_TARGET_SEC * fps))
    n_push   = max(1, int(ZOOM_ANIM_PUSH_SEC   * fps))
    n_hold   = max(1, int(ZOOM_ANIM_HOLD_SEC   * fps))
    n_fade   = max(1, int(ZOOM_ANIM_FADE_SEC   * fps))

    zoom_full = cv2.resize(zoom_result, (W, H), interpolation=cv2.INTER_LANCZOS4)
    frames: list[np.ndarray] = []

    # Фаза 1: прицел
    for i in range(n_target):
        f = src_frame.copy()
        alpha = min(1.0, (i + 1) / max(1, n_target // 2))
        _draw_reticle(f, x1, y1, x2, y2, center_x, center_y, alpha, scale)
        frames.append(f)

    # Фаза 2: Ken Burns — наезд
    for i in range(n_push):
        t = (i + 1) / n_push
        t = t * t * (3 - 2 * t)  # smoothstep
        ax1 = int(x1 * t)
        ay1 = int(y1 * t)
        ax2 = int(W - (W - x2) * t)
        ay2 = int(H - (H - y2) * t)
        if ax2 - ax1 < 4 or ay2 - ay1 < 4:
            frames.append(zoom_full.copy())
            continue
        crop = src_frame[ay1:ay2, ax1:ax2]
        frames.append(cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR))

    # Фаза 3: удержание zoom_result
    label     = "ZOOM x2"
    font_size = max(14, int(20 * scale))
    tw, th    = get_text_size(label, font_size)
    pad       = int(10 * scale)
    lx        = W - tw - int(32 * scale)
    ly        = int(32 * scale) + th
    for _ in range(n_hold):
        f = zoom_full.copy()
        roi = f[ly - th - pad: ly + pad, lx - pad: lx + tw + pad]
        if roi.size > 0:
            bg = roi.copy(); bg[:] = (0, 0, 0)
            cv2.addWeighted(bg, 0.6, roi, 0.4, 0, roi)
        put_text(f, label, (lx, ly), font_size, ZOOM_COLOR)
        frames.append(f)

    # Фаза 4: Ken Burns — отъезд (обратный наезд)
    for i in range(n_fade):
        t = 1.0 - (i + 1) / n_fade
        t = t * t * (3 - 2 * t)  # smoothstep
        ax1 = int(x1 * t)
        ay1 = int(y1 * t)
        ax2 = int(W - (W - x2) * t)
        ay2 = int(H - (H - y2) * t)
        if ax2 - ax1 < 4 or ay2 - ay1 < 4:
            frames.append(src_frame.copy())
            continue
        crop = src_frame[ay1:ay2, ax1:ax2]
        frames.append(cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR))

    return frames
