"""
HUD pipe — виджеты телеметрии поверх VideoSegment-ов.

TelemetryQuery      — поиск ближайшего пакета по src_t (bisect)
TurnQuery           — номер витка по src_t
apply_hud           — наложить виджеты на BGR кадр с заданной прозрачностью
process_video_segment — извлечь кадры, наложить HUD, записать video-only mp4
"""
from __future__ import annotations

import bisect
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from pilot.widgets.hud import draw_hud

import cv2

from .parser import ParsedSession
from .rules import Filter, Overlay, OVERLAY_CHARS_PER_SEC, OVERLAY_MIN_SEC
from .timeline import VideoSegment, Timeline


def set_draft_mode() -> None:
    """Черновой режим: заменить GaussianBlur на no-op во всех виджетах."""
    import cv2 as _cv2
    _cv2.GaussianBlur = lambda src, ksize, sigmaX, **kw: src


# ── Телеметрия ────────────────────────────────────────────────────────────────

class TelemetryQuery:
    """Ближайший пакет телеметрии по src_t."""

    def __init__(self, session: ParsedSession) -> None:
        self._frames: list[dict] = []
        self._times:  list[float] = []
        path = session.session_dir / "telemetry.jsonl"
        if not path.exists():
            return
        bts = session.beep_ts_wall
        vbt = session.video_beep_t
        acc = 0.0
        prev_raw_ts = None
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                raw_ts = rec.get("ts", 0.0)
                if prev_raw_ts is not None:
                    acc += rec.get("gyro_z", 0.0) * (raw_ts - prev_raw_ts)
                rec["gyro_angle"] = acc
                prev_raw_ts = raw_ts
                self._times.append((raw_ts - bts) + vbt)
                self._frames.append(rec)

    def at(self, src_t: float) -> dict | None:
        if not self._frames:
            return None
        idx = bisect.bisect_right(self._times, src_t)
        if idx == 0:
            return self._frames[0]
        if idx >= len(self._frames):
            return self._frames[-1]
        if abs(self._times[idx - 1] - src_t) <= abs(self._times[idx] - src_t):
            return self._frames[idx - 1]
        return self._frames[idx]


# ── Токены ────────────────────────────────────────────────────────────────────

def _fmt_tokens(n: int) -> str:
    if n == 0:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class TokenQuery:
    """Накопленные токены по src_t."""

    def __init__(self, events: list[dict]) -> None:
        cumulative = 0
        self._data: list[tuple[float, int]] = []
        for e in sorted(events, key=lambda x: x.get("src_t", 0)):
            if e.get("type") == "api_response_end" and "usage" in e:
                u = e["usage"]
                cumulative += u.get("input_tokens", 0) + u.get("output_tokens", 0)
                self._data.append((e["src_t"], cumulative))

    def at(self, src_t: float) -> int:
        if not self._data:
            return 0
        times = [t for t, _ in self._data]
        idx = bisect.bisect_right(times, src_t) - 1
        return self._data[idx][1] if idx >= 0 else 0


# ── Номер витка ───────────────────────────────────────────────────────────────

class TurnQuery:
    """Текущий номер витка (turn) по src_t."""

    def __init__(self, events: list[dict]) -> None:
        self._data: list[tuple[float, int]] = sorted(
            (e["src_t"], e["turn"]) for e in events if "turn" in e
        )

    def at(self, src_t: float) -> int:
        if not self._data:
            return 0
        times = [t for t, _ in self._data]
        idx = bisect.bisect_right(times, src_t) - 1
        return self._data[idx][1] if idx >= 0 else 0


# ── HUD alpha ─────────────────────────────────────────────────────────────────

def hud_alpha(
    filters:     list[Filter],
    out_filters: list[tuple[str, float, float]],
    src_t:       float,
    out_t:       float,
) -> float:
    """Прозрачность HUD в [0.0, 1.0].

    fade_hud_in  (out_t): post_zoom окно после SynthSegment → плавно 0→1.
    fade_hud_out (src_t): pre_zoom окно, из timeline.filters → плавно 1→0.
    """
    # out_filters — только fade_hud_in (позиция в out_t, после SynthSegment)
    for effect, out_start, out_end in out_filters:
        if out_start <= out_t <= out_end:
            dur = max(out_end - out_start, 1e-6)
            if effect == "fade_hud_in":
                return (out_t - out_start) / dur
            if effect == "fade_hud_out":
                return max(0.0, 1.0 - (out_t - out_start) / dur)

    # timeline.filters — fade_hud_out / hold_hud_off в src_t пространстве
    for f in filters:
        if f.src_start <= src_t <= f.src_end:
            dur = max(f.src_end - f.src_start, 1e-6)
            if f.effect == "fade_hud_out":
                return max(0.0, 1.0 - (src_t - f.src_start) / dur)
            if f.effect == "fade_hud_in":
                return (src_t - f.src_start) / dur
            if f.effect == "hold_hud_off":
                return 0.0

    return 1.0


# ── Оверлей оператора ─────────────────────────────────────────────────────────

_OPERATOR_TITLES = {
    "operator_answer":     "ОТВЕТ ОПЕРАТОРА",
    "operator_initiative": "ОПЕРАТОР",
}

class OverlayQuery:
    """Активный оверлей оператора по out_t."""

    def __init__(self, timeline: Timeline) -> None:
        self._data: list[tuple[float, float, str, str]] = []
        for ov in timeline.overlays:
            out_s = timeline.src_to_out_clamp(ov.src_start)
            out_e = timeline.src_to_out_clamp(ov.src_end)
            if out_e > out_s:
                text         = ov.meta.get("text", "")
                original_dur = out_e - out_s
                proportional = len(text) / OVERLAY_CHARS_PER_SEC
                display_dur  = min(original_dur, max(OVERLAY_MIN_SEC, proportional))
                self._data.append((out_s, out_s + display_dur, ov.kind, text))

    def at(self, out_t: float) -> tuple[str, str] | None:
        for out_s, out_e, kind, text in self._data:
            if out_s <= out_t < out_e:
                return kind, text
        return None


def _draw_operator_overlay(frame: np.ndarray, kind: str, text: str, scale: float) -> None:
    """Центрированный HUD-блок с сообщением оператора."""
    from pilot.widgets._text import get_text_size, put_text
    from pilot.widgets.hud_block import (
        _chamfer_poly, COLOR_BG, COLOR_BORDER, COLOR_TITLE_BG,
        COLOR_TITLE, COLOR_VALUE, ALPHA_BG, CHAMFER,
    )

    H, W   = frame.shape[:2]
    title  = _OPERATOR_TITLES.get(kind, kind.upper())

    font_title = max(10, int(14 * scale))
    font_text  = max(8,  int(13 * scale))
    pad        = int(10 * scale)
    title_h    = int(28 * scale)
    row_h      = int(22 * scale)
    block_w    = int(320 * scale)
    max_text_w = block_w - 2 * pad

    words = text.split() if text else []
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
    if not lines:
        lines = [""]

    total_h  = title_h + 6 + len(lines) * row_h + pad + 4
    x        = int(W - block_w - 24 * scale)
    y        = int(24 * scale)
    x2       = x + block_w
    y2       = y + total_h
    title_y2 = y + title_h + 2
    c        = max(4, int(CHAMFER * scale))
    thick    = max(1, int(scale))

    poly       = _chamfer_poly(x, y, x2, y2, c)
    title_poly = _chamfer_poly(x, y, x2, title_y2, c)

    ov = frame.copy()
    cv2.fillPoly(ov, [poly], COLOR_BG)
    cv2.addWeighted(ov, ALPHA_BG, frame, 1 - ALPHA_BG, 0, frame)

    ov2 = frame.copy()
    cv2.fillPoly(ov2, [title_poly], COLOR_TITLE_BG)
    cv2.addWeighted(ov2, 0.75, frame, 0.25, 0, frame)

    glow = np.zeros_like(frame)
    cv2.polylines(glow, [poly], isClosed=True, color=COLOR_BORDER,
                  thickness=thick, lineType=cv2.LINE_AA)
    blurred = cv2.GaussianBlur(glow, (9, 9), 0)
    frame[:] = np.clip(
        frame.astype(np.int16) + blurred.astype(np.int16), 0, 255
    ).astype(np.uint8)
    cv2.polylines(frame, [poly], isClosed=True, color=COLOR_BORDER,
                  thickness=thick, lineType=cv2.LINE_AA)

    put_text(frame, title, (x + pad, y + title_h - pad // 2), font_title, COLOR_TITLE)

    for i, ln in enumerate(lines):
        yt = title_y2 + 6 + (i + 1) * row_h - (row_h - font_text) // 2
        put_text(frame, ln, (x + pad, yt), font_text, COLOR_VALUE)


# ── Виджеты ───────────────────────────────────────────────────────────────────

def apply_hud(
    frame:   np.ndarray,
    t:       dict | None,
    turn_n:  int,
    alpha:   float,
    scale:   float,
    tokens:  int = 0,
    title:   str = "",
    overlay: tuple[str, str] | None = None,
) -> None:
    """Наложить HUD на BGR кадр.

    alpha=1.0 — полный HUD; alpha=0.0 — только overlay (если есть).
    При 0 < alpha < 1 — смешиваем с исходным кадром.
    overlay рисуется поверх HUD всегда на полной непрозрачности.
    """
    if alpha <= 0.0 and overlay is None:
        return

    range_mm    = t.get("range_mm",      0)   if t else 0
    voltage     = t.get("voltage",       0.0) if t else 0.0
    l_speed     = t.get("left_speed",    0)   if t else 0
    r_speed     = t.get("right_speed",   0)   if t else 0
    l_curr      = t.get("left_current",  0)   if t else 0
    r_curr      = t.get("right_current", 0)   if t else 0
    heading_str = f"{t['gyro_angle']:.0f} deg" if t and "gyro_angle" in t else "—"

    kwargs = dict(
        range_mm=range_mm, voltage=voltage, heading_str=heading_str,
        left_speed=l_speed, right_speed=r_speed,
        left_current=l_curr, right_current=r_curr,
        scale=scale,
        title=title,
        rows=[
            ("Turn", str(turn_n)),
            *([("Tokens", _fmt_tokens(tokens))] if tokens > 0 else []),
        ],
        led_red=bool(t.get("led_red",  False)) if t else False,
        led_blue=bool(t.get("led_blue", False)) if t else False,
    )

    if alpha > 0.0:
        if alpha < 1.0:
            tmp = frame.copy()
            draw_hud(tmp, **kwargs)
            frame[:] = np.clip(
                frame.astype(np.float32) * (1.0 - alpha)
                + tmp.astype(np.float32) * alpha,
                0, 255,
            ).astype(np.uint8)
        else:
            draw_hud(frame, **kwargs)

    if overlay is not None:
        _draw_operator_overlay(frame, overlay[0], overlay[1], scale)


# ── Speech query (для debug-оверлея) ─────────────────────────────────────────

class SpeechQuery:
    """Ближайший к out_t аудиофайл (речь Polly). Только для --debug."""

    def __init__(self, audio_mixes: list[tuple[Path, float, float]]) -> None:
        self._data = sorted(audio_mixes, key=lambda x: x[2])  # по out_t

    def at(self, out_t: float) -> tuple[str, float, float] | None:
        """Последний стартовавший файл: (name, src_t, mix_out_t) или None."""
        result = None
        for path, src_t, mix_out_t in self._data:
            if mix_out_t <= out_t:
                result = (path.name, src_t, mix_out_t)
            else:
                break
        return result


# ── Debug timecode ────────────────────────────────────────────────────────────

def _draw_debug_timecode(
    frame:   np.ndarray,
    src_t:   float,
    out_t:   float,
    speech:  tuple[str, float, float] | None = None,
) -> None:
    """Нарисовать src_t / out_t и последний speech-файл внизу по центру (--debug)."""
    from pilot.widgets._text import put_text, get_text_size
    H, W = frame.shape[:2]
    size_px = 18
    line1 = f"src={src_t:.3f}  out={out_t:.3f}"
    line2 = (f"{speech[0]}  src={speech[1]:.3f}  out={speech[2]:.3f}"
             if speech else "")
    _, th = get_text_size(line1, size_px)
    n_lines  = 2 if line2 else 1
    strip_h  = th * n_lines + 6 * (n_lines + 1)
    frame[H - strip_h:H] = (frame[H - strip_h:H] * 0.4).astype(np.uint8)
    y1 = H - 6 - (th + 6) * (n_lines - 1)
    tw1, _ = get_text_size(line1, size_px)
    put_text(frame, line1, (max(0, (W - tw1) // 2), y1), size_px, (0, 255, 65))
    if line2:
        tw2, _ = get_text_size(line2, size_px)
        put_text(frame, line2, (max(0, (W - tw2) // 2), H - 6), size_px, (0, 200, 255))


# ── Рендер сегмента ───────────────────────────────────────────────────────────

def process_video_segment(
    seg:         VideoSegment,
    video_path:  Path,
    telemetry:   TelemetryQuery,
    turns:       TurnQuery,
    filters:     list[Filter],
    out_filters: list[tuple[str, float, float]],
    fps:         float,
    scale:       float,
    W:           int,
    H:           int,
    out_path:    Path,
    token_query:   TokenQuery   | None = None,
    title:         str = "",
    overlay_query: OverlayQuery | None = None,
    speech_query:  SpeechQuery  | None = None,
    debug_overlay: bool = False,
) -> None:
    """Извлечь кадры VideoSegment из video_path, наложить HUD, записать video-only mp4.

    Цикл ограничен expected_frames — обрезает лишние кадры от keyframe alignment.
    Аудио не пишется (-an): сборка аудио происходит глобально в render.build_audio().
    """
    frame_size      = W * H * 3
    expected_frames = round((seg.src_end - seg.src_start) * fps)

    reader = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-ss", str(seg.src_start), "-to", str(seg.src_end),
            "-i", str(video_path),
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    writer = subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0",
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    frame_idx = 0
    while frame_idx < expected_frames:
        data = reader.stdout.read(frame_size)
        if len(data) < frame_size:
            break
        frame  = np.frombuffer(data, dtype=np.uint8).reshape((H, W, 3)).copy()
        src_t  = min(seg.src_start + frame_idx / fps, seg.src_end)
        out_t  = min(seg.out_start + frame_idx / fps, seg.out_end)
        alpha  = hud_alpha(filters, out_filters, src_t, out_t)
        tokens  = token_query.at(src_t)   if token_query   else 0
        overlay = overlay_query.at(out_t) if overlay_query else None
        apply_hud(frame, telemetry.at(src_t), turns.at(src_t), alpha, scale, tokens, title, overlay)
        if debug_overlay:
            speech_info = speech_query.at(out_t) if speech_query else None
            _draw_debug_timecode(frame, src_t, out_t, speech_info)
        writer.stdin.write(frame.tobytes())
        frame_idx += 1
        if frame_idx % 100 == 0:
            print(f"\r  кадр {frame_idx} / src={src_t:.1f}s", end="", flush=True)

    writer.stdin.close()
    reader.stdout.read()  # сбросить лишние кадры, избежать SIGPIPE
    reader.wait()
    writer.wait()
    if frame_idx > 0:
        print()
