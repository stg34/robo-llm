"""
FSM — машина состояний постобработки видео.

Разбивает timeline событий на сегменты: active, api_pause, zoom_pause.
Каждый сегмент — непересекающийся интервал [vt_start, vt_end) с описанием
что делать с видео на этом участке.

Измерения FSM:
  api_state:      IDLE | THINKING
  movement_state: IDLE | MOVING | TURNING
  speech_state:   SILENT | SPEAKING
  operator_state: NONE | ASKING | ANSWERED | WAITING_MSG

Таблица отображений (state → kind):
  api=THINKING + pending_zoom  → zoom_pause
  api=THINKING                 → api_pause
  иначе                        → active

Таблица отображений (state → overlay):
  operator=ANSWERED            → "ОТВЕТ ОПЕРАТОРА"
  operator=WAITING_MSG         → "ОПЕРАТОР"
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Literal


@dataclass
class Segment:
    """Отрезок видео с аннотацией действия."""
    vt_start: float
    vt_end:   float
    kind:     Literal["active", "api_pause", "zoom_pause"]

    # Для рендера active-кадров
    hud_action:       str                    = "idle"
    subtitle:         str                    = ""
    operator_overlay: tuple[str, str] | None = None  # (text, title)
    turn_n:           int                    = 0
    tokens:           int | None             = None

    # Для zoom_pause
    zoom_cx:  int = 0
    zoom_cy:  int = 0
    zoom_num: int = 0

    @property
    def duration(self) -> float:
        return self.vt_end - self.vt_start

    def __repr__(self) -> str:
        d = f"{self.duration:.2f}s"
        s = f"Segment({self.kind} {self.vt_start:.2f}–{self.vt_end:.2f} [{d}]"
        if self.kind == "active":
            s += f" hud={self.hud_action}"
            if self.operator_overlay:
                s += f" op={self.operator_overlay[1]!r}"
        elif self.kind == "zoom_pause":
            s += f" zoom={self.zoom_num}"
        return s + ")"


def find_segment(segments: list[Segment], vt: float) -> Segment | None:
    """Бинарный поиск сегмента для момента vt."""
    if not segments:
        return None
    idx = bisect.bisect_right([s.vt_start for s in segments], vt) - 1
    if idx < 0:
        return None
    seg = segments[idx]
    if seg.vt_start <= vt < seg.vt_end:
        return seg
    return None


def _inject_operator_waits(timeline: list[dict]) -> list[dict]:
    """Добавить синтетические operator_wait_start для -i режима.

    В -i режиме период ожидания ввода оператора не имеет явного события начала.
    Инжектируем operator_wait_start в момент last tool_end/api_response_end,
    после которого нет api_request_start до operator_message.
    """
    extra: list[dict] = []
    last_active_vt: float | None = None
    has_request = False

    for e in timeline:
        t = e["type"]
        if t in ("tool_end", "api_response_end"):
            last_active_vt = e["vt"]
            has_request = False
        elif t == "api_request_start":
            has_request = True
        elif (t == "operator_message"
              and last_active_vt is not None
              and not has_request):
            text = e.get("text", "").strip()
            if text:
                extra.append({
                    "type": "operator_wait_start",
                    "vt":   last_active_vt,
                    "text": text,
                    "ts":   e["ts"],
                })

    if not extra:
        return timeline

    merged = timeline + extra
    # При одинаковом vt реальные события идут раньше синтетических
    merged.sort(key=lambda e: (e["vt"], e["type"] == "operator_wait_start"))
    return merged


def build_segments(timeline: list[dict], duration: float,
                   no_cut: bool = False) -> list[Segment]:
    """Разбить timeline событий на сегменты.

    no_cut=True: паузы не вырезаются — все сегменты active
                 (hud_action="thinking" на периодах ожидания Claude).
    """
    # Пре-вычислим ответы ask_human — нужны при tool_start,
    # до того как speech_end переключит operator_state в ANSWERED.
    ask_answers: dict[int, str] = {}
    for e in timeline:
        if e["type"] == "tool_end" and e.get("tool") == "ask_human":
            answer = (e.get("result") or {}).get("answer", "").strip()
            if answer and answer != "(нет ответа)":
                ask_answers[e.get("turn", -1)] = answer

    events = _inject_operator_waits(timeline)
    segments: list[Segment] = []

    # ── Состояния FSM ────────────────────────────────────────────────────────
    api_state      = "IDLE"    # IDLE | THINKING
    movement_state = "IDLE"    # IDLE | MOVING | TURNING
    speech_state   = "SILENT"  # SILENT | SPEAKING
    operator_state = "NONE"    # NONE | ASKING | ANSWERED | WAITING_MSG
    operator_text  = ""
    subtitle       = ""
    hud_action     = "idle"
    turn_n         = 0
    tokens:        int | None = None
    pending_zoom:  dict | None = None  # zoom ожидает привязки к паузе
    zoom_inp:      dict        = {}    # input из tool_start zoom
    zoom_counter   = 0
    seg_start      = 0.0

    # ── Вспомогательные функции ───────────────────────────────────────────────

    def _kind() -> Literal["active", "api_pause", "zoom_pause"]:
        if not no_cut and api_state == "THINKING":
            return "zoom_pause" if pending_zoom else "api_pause"
        return "active"

    def _overlay() -> tuple[str, str] | None:
        if operator_state == "ANSWERED" and operator_text:
            return operator_text, "ОТВЕТ ОПЕРАТОРА"
        if operator_state == "WAITING_MSG" and operator_text:
            return operator_text, "ОПЕРАТОР"
        return None

    def _hud() -> str:
        if movement_state == "MOVING":  return "move"
        if movement_state == "TURNING": return "turn"
        if speech_state == "SPEAKING":  return "speaking"
        return hud_action

    def flush(vt_end: float) -> None:
        nonlocal seg_start
        if vt_end <= seg_start:
            return
        kind = _kind()
        seg  = Segment(
            vt_start=seg_start, vt_end=vt_end, kind=kind,
            turn_n=turn_n, tokens=tokens,
        )
        if kind == "active":
            seg.hud_action       = _hud()
            seg.subtitle         = subtitle
            seg.operator_overlay = _overlay()
        elif kind == "zoom_pause" and pending_zoom:
            seg.zoom_cx  = pending_zoom["cx"]
            seg.zoom_cy  = pending_zoom["cy"]
            seg.zoom_num = pending_zoom["num"]
        segments.append(seg)
        seg_start = vt_end

    # ── Главный цикл FSM ──────────────────────────────────────────────────────

    for e in events:
        vt = e["vt"]
        t  = e["type"]

        flush(vt)  # закрываем текущий сегмент перед изменением состояния

        if t == "api_request_start":
            api_state  = "THINKING"
            turn_n    += 1
            hud_action = "thinking"

        elif t == "api_response_end":
            pending_zoom = None  # zoom потреблён этой паузой (или не было)
            api_state    = "IDLE"
            hud_action   = "idle"
            usage = e.get("usage") or {}
            if usage:
                tokens = (tokens or 0) + (usage.get("input_tokens", 0)
                                          + usage.get("output_tokens", 0))

        elif t == "tool_start":
            tool = e.get("tool", "")
            if tool == "move":
                movement_state = "MOVING"
                hud_action     = "move"
            elif tool in ("turn_relative", "turn_absolute"):
                movement_state = "TURNING"
                hud_action     = "turn"
            elif tool == "done":
                hud_action = "done"
            elif tool == "zoom":
                zoom_inp = e.get("input") or {}
            elif tool == "ask_human":
                operator_state = "ASKING"
                operator_text  = ask_answers.get(e.get("turn", -1), "")

        elif t == "tool_end":
            tool = e.get("tool", "")
            if tool in ("move", "turn_relative", "turn_absolute"):
                movement_state = "IDLE"
                hud_action     = "idle"
            elif tool == "zoom":
                zoom_counter += 1
                pending_zoom  = {
                    "cx":  zoom_inp.get("cx", 0),
                    "cy":  zoom_inp.get("cy", 0),
                    "num": zoom_counter,
                }
                zoom_inp = {}
            elif tool == "ask_human":
                operator_state = "NONE"
                operator_text  = ""

        elif t == "speech_start":
            speech_state = "SPEAKING"
            subtitle     = e.get("text", "")
            hud_action   = "speaking"

        elif t == "speech_end":
            speech_state = "SILENT"
            subtitle     = ""
            if hud_action == "speaking":
                hud_action = "idle"
            if operator_state == "ASKING":
                operator_state = "ANSWERED"  # робот закончил спрашивать

        elif t == "operator_wait_start":
            operator_state = "WAITING_MSG"
            operator_text  = e.get("text", "")

        elif t == "operator_message":
            if operator_state == "WAITING_MSG":
                operator_state = "NONE"
                operator_text  = ""

    flush(duration)
    return segments
