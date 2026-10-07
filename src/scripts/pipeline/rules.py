"""
Layer 2 — Rule engine.

Вход:  ParsedSession (из parser.py)
Выход: list[Operation]

Правило: функция (StateInterval, actor, StateQuery, ParsedSession) → list[Operation]
Регистрация: @rule("actor_name")

Добавить новое поведение = написать функцию + декоратор. Граф и рендер не трогаем.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .parser import ParsedSession, StateInterval


# ── Операции ─────────────────────────────────────────────────────────────────

@dataclass
class Cut:
    """Вырезать интервал из видео."""
    src_start: float
    src_end:   float


@dataclass
class Insert:
    """Вставить синтетический сегмент.

    anchor="at"             — вставить точно в src_t
    anchor="after_next_cut" — вставить в начало ближайшего Cut после src_t
                              (позицию разрешает Layer 3)
    """
    src_t:  float
    kind:   str
    anchor: str  = "at"    # "at" | "after_next_cut"
    meta:   dict = field(default_factory=dict)


@dataclass
class Filter:
    """Применить видеофильтр на интервале."""
    src_start: float
    src_end:   float
    effect:    str   # "fade_hud_out", "fade_hud_in", ...
    meta:      dict  = field(default_factory=dict)


@dataclass
class MixAudio:
    """Подмешать аудиофайл в позицию src_t."""
    src_t: float
    path:  Path
    meta:  dict = field(default_factory=dict)


@dataclass
class Overlay:
    """Наложить информационный оверлей на интервал."""
    src_start: float
    src_end:   float
    kind:      str   # "operator_answer" | "operator_initiative"
    meta:      dict  = field(default_factory=dict)


Operation = Cut | Insert | Filter | MixAudio | Overlay


# ── StateQuery ────────────────────────────────────────────────────────────────

class StateQuery:
    """Эффективный поиск состояния актора в момент src_t."""

    def __init__(self, actors: dict[str, list[StateInterval]]) -> None:
        # Кэшируем отсортированные начала для bisect
        self._actors  = actors
        self._starts  = {
            actor: [iv.src_start for iv in ivs]
            for actor, ivs in actors.items()
        }

    def state_at(self, actor: str, src_t: float) -> str:
        """Состояние актора в момент src_t. Возвращает '' если вне диапазона.

        При одинаковом src_start у нескольких интервалов (standing + post_zoom)
        bisect может попасть на более короткий — идём назад до первого покрывающего.
        """
        ivs    = self._actors.get(actor, [])
        starts = self._starts.get(actor, [])
        if not ivs:
            return ""
        idx = bisect.bisect_right(starts, src_t) - 1
        while idx >= 0:
            iv = ivs[idx]
            if iv.src_start <= src_t < iv.src_end:
                return iv.state
            idx -= 1
        return ""

    def intervals_of(self, actor: str, state: str) -> list[StateInterval]:
        """Все интервалы актора в данном состоянии."""
        return [iv for iv in self._actors.get(actor, []) if iv.state == state]


# ── Регистр правил ────────────────────────────────────────────────────────────

RuleFn = Callable[
    [StateInterval, str, StateQuery, ParsedSession],
    list[Operation]
]

_RULES: list[tuple[str, RuleFn]] = []


def rule(actor: str):
    """Декоратор регистрации правила для конкретного актора."""
    def decorator(fn: RuleFn) -> RuleFn:
        _RULES.append((actor, fn))
        return fn
    return decorator


# ── Правила ───────────────────────────────────────────────────────────────────

# Паузы, которые нельзя резать, несмотря на ai=thinking + robot=standing.
# Ключ — имя сессии, значение — список src_start интервалов thinking (сек).
# Сессия gun: пауза сразу после выстрела (64.05с) — на видео в этот момент
# разлетается зеркало, момент нужно сохранить.
KEEP_THINKING_PAUSES: dict[str, list[float]] = {
    "brain_20260526_110436": [64.051],
}
_KEEP_TOL = 0.5  # допуск совпадения src_start, сек


@rule("ai")
def cut_thinking(iv: StateInterval, actor: str, q: StateQuery,
                 session: ParsedSession) -> list[Operation]:
    """AI думает + робот стоит → вырезать паузу.

    Cut покрывает только sub-интервал где robot=standing.
    pre_zoom и post_zoom не трогаем — они нужны для HUD-фейда.
    Если thinking стартует в post_zoom — откладываем Cut до конца post_zoom.
    Обрезаем Cut перед первым pre_zoom/zooming внутри интервала.
    Исключение: паузы из KEEP_THINKING_PAUSES сохраняются (не режутся).
    """
    if iv.state != "thinking":
        return []

    for keep_t in KEEP_THINKING_PAUSES.get(session.session_dir.name, []):
        if abs(iv.src_start - keep_t) <= _KEEP_TOL:
            return []

    start_state = q.state_at("robot", iv.src_start)
    if start_state not in ("standing", "post_zoom"):
        return []

    cut_start = iv.src_start
    cut_end   = iv.src_end

    if start_state == "post_zoom":
        for r in session.actors.get("robot", []):
            if r.state == "post_zoom" and r.src_start <= iv.src_start < r.src_end:
                cut_start = r.src_end
                break

    for r in session.actors.get("robot", []):
        if r.state in ("pre_zoom", "zooming") and cut_start < r.src_start < cut_end:
            cut_end = r.src_start

    if cut_end > cut_start:
        return [Cut(cut_start, cut_end)]
    return []


@rule("robot")
def zoom_animation(iv: StateInterval, actor: str, q: StateQuery,
                   session: ParsedSession) -> list[Operation]:
    """Зум → вставить анимацию после конца зума; Layer 3 привяжет к ближайшему Cut."""
    if iv.state != "zooming":
        return []
    return [Insert(iv.src_end, "zoom_animation",
                   anchor="after_next_cut", meta=dict(iv.meta))]


FADE_OUT_SEC = 0.8
FADE_IN_SEC  = 0.8


def _next_cut_start_after(session: ParsedSession, q: StateQuery, t: float) -> float:
    """Найти src_t начала ближайшего Cut после t.

    Смотрим на два источника Cut-операций:
    - cut_thinking:     ai=thinking → Cut начинается в thinking.src_start
    - operator_overlay: waiting_msg → Cut(src_start, src_end - display_dur),
                        т.е. Cut начинается в op_iv.src_start.
                        Если t уже внутри этого Cut — зазора нет, возвращаем t.
    """
    candidates: list[float] = []

    for ai_iv in session.actors.get("ai", []):
        if ai_iv.state != "thinking":
            continue
        # Зеркалим логику cut_thinking: если thinking стартует в post_zoom,
        # фактический Cut начинается в post_zoom.src_end, а не в thinking.src_start.
        robot_state = q.state_at("robot", ai_iv.src_start)
        if robot_state == "post_zoom":
            cut_s = ai_iv.src_start
            for r in session.actors.get("robot", []):
                if r.state == "post_zoom" and r.src_start <= ai_iv.src_start < r.src_end:
                    cut_s = r.src_end
                    break
        elif robot_state == "standing":
            cut_s = ai_iv.src_start
        else:
            continue  # этот thinking не порождает Cut
        if cut_s <= t + 0.05:
            if ai_iv.src_end > t - 0.05:
                return t  # Cut содержит или начинается в t — зазора нет
            continue  # Cut уже прошёл, ищем следующий
        candidates.append(cut_s)
        break

    for op_iv in session.actors.get("operator", []):
        if op_iv.state != "waiting_msg":
            continue
        orig_dur   = op_iv.src_end - op_iv.src_start
        disp_dur   = _operator_display_dur(op_iv.meta.get("text", ""), orig_dur)
        cut_end    = op_iv.src_end - disp_dur   # конец Cut = начало оверлея
        cut_start  = op_iv.src_start
        if cut_start <= t <= cut_end:
            return t  # t уже внутри Cut — зазора нет
        if cut_start > t - 0.05:
            candidates.append(cut_start)
            break  # первый Cut после t — дальше не смотрим
        # Cut до t — ищем следующий waiting_msg

    valid = [c for c in candidates if c > t + 0.05]
    return min(valid) if valid else t


@rule("robot")
def pre_zoom_fade(iv: StateInterval, actor: str, q: StateQuery,
                  session: ParsedSession) -> list[Operation]:
    """Окно перед зумом → плавное скрытие HUD.

    Фейд заканчивается на post_zoom.src_end. Если VideoSegment продолжается
    дальше (например, Cut начинается позже из-за waiting_msg оператора),
    добавляем hold_hud_off на весь этот зазор.
    """
    if iv.state != "pre_zoom":
        return []
    filter_end = iv.src_end
    for r in session.actors.get("robot", []):
        if r.state == "post_zoom" and r.src_start >= iv.src_end - 0.05:
            filter_end = r.src_end
            break
    fade_start = max(iv.src_start, filter_end - FADE_OUT_SEC)
    ops: list[Operation] = [Filter(fade_start, filter_end, "fade_hud_out")]

    hold_until = _next_cut_start_after(session, q, filter_end)
    if hold_until > filter_end + 0.1:
        ops.append(Filter(filter_end, hold_until, "hold_hud_off"))

    return ops



@rule("speech")
def mix_speech(iv: StateInterval, actor: str, q: StateQuery,
               session: ParsedSession) -> list[Operation]:
    """Речь → подмешать соответствующий MP3.

    filename берётся из события speech_start (поле filename, все новые сессии).
    Fallback на порядковый номер — для очень старых сессий без поля filename,
    при условии что session.jsonl исправлен fix_session.py --ask-human.
    """
    if iv.state != "speaking":
        return []
    speech_starts = [e for e in session.events if e.get("type") == "speech_start"]
    for idx, e in enumerate(speech_starts):
        if abs(e["src_t"] - iv.src_start) < 0.1:
            filename = e.get("filename") or e.get("file") or f"speech_{idx + 1:03d}.mp3"
            path = session.session_dir / filename
            if path.exists():
                return [MixAudio(iv.src_start, path, {"text": iv.meta.get("text", "")})]
    return []


# Параметры длительности оверлея оператора (используются также в hud_pipe.py)
OVERLAY_CHARS_PER_SEC = 15.0
OVERLAY_MIN_SEC       = 3.0


def _operator_display_dur(text: str, original_dur: float) -> float:
    """Длительность показа оверлея: пропорционально тексту, не меньше минимума,
    не больше оригинального интервала."""
    proportional = len(text) / OVERLAY_CHARS_PER_SEC
    return min(original_dur, max(OVERLAY_MIN_SEC, proportional))


@rule("operator")
def operator_overlay(iv: StateInterval, actor: str, q: StateQuery,
                     session: ParsedSession) -> list[Operation]:
    """Оператор отвечает или берёт инициативу → оверлей.

    Оба случая: оверлей в конце интервала (src_end - display_dur .. src_end),
    голова (пока оператор печатает) вырезается Cut(src_start, src_end - display_dur).
    """
    if iv.state == "answered":
        text         = iv.meta.get("text", "")
        original_dur = iv.src_end - iv.src_start
        display_dur  = _operator_display_dur(text, original_dur)
        overlay_start = iv.src_end - display_dur
        return [Overlay(overlay_start, iv.src_end, "operator_answer", {"text": text})]
    if iv.state == "waiting_msg":
        text         = iv.meta.get("text", "")
        original_dur = iv.src_end - iv.src_start
        display_dur  = _operator_display_dur(text, original_dur)
        overlay_start = iv.src_end - display_dur
        ops: list[Operation] = [
            Overlay(overlay_start, iv.src_end, "operator_initiative", {"text": text}),
        ]
        if overlay_start > iv.src_start + 0.1:
            ops.append(Cut(iv.src_start, overlay_start))
        return ops
    return []


# ── Engine ────────────────────────────────────────────────────────────────────

def apply_rules(session: ParsedSession) -> list[Operation]:
    """
    Прогнать все правила по интервалам сессии.
    Возвращает список операций, отсортированных по src_t.
    """
    q   = StateQuery(session.actors)
    ops: list[Operation] = []

    for actor, fn in _RULES:
        for iv in session.actors.get(actor, []):
            ops.extend(fn(iv, actor, q, session))

    # Сортировка по src_t (у каждой операции есть src_start или src_t)
    def _key(op: Operation) -> float:
        if isinstance(op, (Cut, Filter, Overlay)):
            return op.src_start
        return op.src_t  # Insert, MixAudio

    ops.sort(key=_key)
    return ops
