"""
Общий HUD: одна функция draw_hud() для FPV-режима, AI-режима и постобработки.

Принимает плоские параметры — вызывающая сторона сама извлекает их
из TelemetryFrame или dict в зависимости от контекста.
"""
from __future__ import annotations

import numpy as np

import pilot.config as cfg
from pilot.widgets.angle_ruler import draw_angle_ruler
from pilot.widgets.drive_indicator import draw_drive_indicator
from pilot.widgets.hud_block import draw_hud_block
from pilot.widgets.led_indicator import draw_led_indicator
from pilot.widgets.rangefinder import draw_rangefinder


def draw_hud(
    frame:        np.ndarray,
    *,
    range_mm:     int,
    voltage:      float,
    heading_str:  str,
    left_speed:   int,
    right_speed:  int,
    left_current: int,
    right_current: int,
    scale:        float,
    title:        str,
    rows:         list[tuple[str, str]],
    led_red:      bool = False,
    led_blue:     bool = False,
) -> None:
    """Наложить полный HUD поверх BGR-кадра.

    range_mm, voltage и т.д. — плоские значения телеметрии.
    rows      — дополнительные строки hud_block после стандартных Voltage/Heading/Range.
    """
    H, W = frame.shape[:2]

    draw_angle_ruler(frame, scale=scale)

    rf_cx, rf_cy = cfg.rangefinder_pos(frame.shape, range_mm)
    draw_rangefinder(frame, range_mm, scale=scale, cx=rf_cx, cy=rf_cy,
                     radius=cfg.rangefinder_radius(range_mm))

    ind_w  = int(45 * scale)
    margin = int(cfg.HUD_BLOCK_X * scale)
    ind_y  = H - int(246 * scale)
    draw_drive_indicator(frame, x=margin, y=ind_y,
                         label="L", speed=left_speed,
                         current_adc=left_current, scale=scale)
    draw_drive_indicator(frame, x=W - margin - ind_w, y=ind_y,
                         label="R", speed=right_speed,
                         current_adc=right_current, scale=scale)

    if cfg.HUD_SHOW_LIGHTS:
        led_w = int(80 * scale)
        led_h = int(62 * scale)
        led_x = margin + ind_w + int(16 * scale)
        led_y = H - led_h - int(27 * scale)
        draw_led_indicator(frame, led_x, led_y, red=led_red, blue=led_blue, scale=scale)

    draw_hud_block(
        frame,
        x=int(cfg.HUD_BLOCK_X * scale), y=int(cfg.HUD_BLOCK_Y * scale),
        title=title,
        rows=[
            ("Voltage", f"{voltage:.1f} V"),
            ("Heading", heading_str),
            ("Range",   f"{range_mm} mm"),
            *rows,
        ],
        scale=scale,
    )
