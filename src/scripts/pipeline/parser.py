"""
Layer 1 — парсер логов.

Вход:  session_dir (session.jsonl + video duration)
Выход: ParsedSession — state intervals per actor + raw events с src_t

Два пространства времени:
  ts_wall  — Unix timestamp из session.jsonl
  src_t    — время в видеофайле (секунды от начала)

Якорь синхронизации: событие sync_beep содержит ts_wall бипа;
бип найден в аудио → video_beep_t. Тогда:
  src_t = (ts_wall - beep_ts_wall) + video_beep_t
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from pilot.tools import TOOL_STATES


# ── Структуры данных ──────────────────────────────────────────────────────────

@dataclass
class StateInterval:
    """Интервал [src_start, src_end) в котором актор находится в данном state."""
    src_start: float
    src_end:   float
    state:     str
    meta:      dict = field(default_factory=dict)  # текст речи, zoom coords и т.п.

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start

    def __repr__(self) -> str:
        return (f"StateInterval({self.state!r} "
                f"{self.src_start:.2f}–{self.src_end:.2f} "
                f"[{self.duration:.2f}s]"
                + (f" {self.meta}" if self.meta else "") + ")")


@dataclass
class ParsedSession:
    """Результат парсинга одной сессии."""
    session_dir:    Path
    duration:       float                          # длина видео в секундах
    beep_ts_wall:   float                          # ts_wall бипа
    video_beep_t:   float                          # src_t бипа в видео
    events:         list[dict]                     # raw события с добавленным src_t
    actors:         dict[str, list[StateInterval]] # per-actor интервалы
    model:          str                            # модель из session_start


# ── Загрузка событий ──────────────────────────────────────────────────────────

def load_events(session_dir: Path) -> list[dict]:
    """Читает session.jsonl, возвращает список событий (dict с полем 'type')."""
    path = session_dir / "session.jsonl"
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _find_model(events: list[dict]) -> str:
    for e in events:
        if e.get("type") == "session_start":
            return e.get("model", "")
    return ""


def _find_beep(events: list[dict]) -> float | None:
    """Извлекает ts_wall бипа из события sync_beep."""
    for e in events:
        if e.get("type") == "sync_beep":
            return e.get("ts_wall") or e.get("ts")
    return None


def _to_src_t(ts_wall: float, beep_ts_wall: float, video_beep_t: float) -> float:
    return (ts_wall - beep_ts_wall) + video_beep_t



def annotate_src_t(events: list[dict], beep_ts_wall: float,
                   video_beep_t: float) -> list[dict]:
    """Добавляет поле src_t к каждому событию, сортирует по src_t."""
    result = []
    for e in events:
        ts = e.get("ts_wall") or e.get("ts", 0.0)
        result.append({**e, "src_t": _to_src_t(ts, beep_ts_wall, video_beep_t)})
    result.sort(key=lambda e: e["src_t"])
    return result


# ── FSM ───────────────────────────────────────────────────────────────────────

def _build_actor_intervals(events: list[dict],
                            duration: float) -> dict[str, list[StateInterval]]:
    """
    Прогоняет события через FSM и выдаёт state intervals per actor.

    Акторы и их состояния:
      ai:       idle | thinking
      robot:    standing | moving | turning | zooming | shooting
      speech:   silent | speaking
      operator: idle | asking | answered | waiting_msg
    """
    # Текущее состояние каждого актора
    states = {
        "ai":       "idle",
        "robot":    "standing",
        "speech":   "silent",
        "operator": "idle",
    }
    # Аккумулятор: actor → [(src_start, state, meta)]
    open_intervals: dict[str, tuple[float, str, dict]] = {
        actor: (0.0, state, {}) for actor, state in states.items()
    }
    result: dict[str, list[StateInterval]] = {a: [] for a in states}

    # Мета-данные текущего speech/zoom/operator
    speech_text   = ""
    zoom_input:   dict = {}
    operator_text = ""
    last_idle_src_t = 0.0  # момент последнего api_response_end или tool_end
    # Для ANSWERED: сохраняем ответ оператора при tool_end ask_human
    ask_answers: dict[int, str] = {}
    for e in events:
        if e.get("type") == "tool_end" and e.get("tool") == "ask_human":
            ans = (e.get("result") or {}).get("answer", "").strip()
            if ans and ans != "(нет ответа)":
                ask_answers[e.get("turn", -1)] = ans

    def _flush(actor: str, src_end: float) -> None:
        src_start, state, meta = open_intervals[actor]
        if src_end > src_start:
            result[actor].append(StateInterval(src_start, src_end, state, dict(meta)))

    def _transition(actor: str, new_state: str, src_t: float,
                    meta: dict | None = None) -> None:
        _flush(actor, src_t)
        open_intervals[actor] = (src_t, new_state, meta or {})

    for e in events:
        t     = e["type"]
        src_t = e["src_t"]

        if t == "api_request_start":
            _transition("ai", "thinking", src_t)
            # Оператор передал инициативу — AI начал обрабатывать сообщение
            if open_intervals["operator"][1] == "waiting_msg":
                _transition("operator", "idle", src_t)

        elif t == "api_response_end":
            _transition("ai", "idle", src_t)
            last_idle_src_t = src_t

        elif t == "tool_start":
            tool = e.get("tool", "")
            if tool not in TOOL_STATES:
                raise ValueError(
                    f"Неизвестный инструмент: {tool!r}. "
                    f"Добавь в TOOL_STATES в pilot/tools.py"
                )
            cfg  = TOOL_STATES[tool]
            inp  = e.get("input") or {}
            meta = {k: inp[k] for k in cfg.get("meta_keys", []) if k in inp}

            if cfg["robot_state"] is not None:
                _transition("robot", cfg["robot_state"], src_t, meta)

            # Специфика тулов с нестандартной логикой
            if tool == "zoom":
                zoom_input = inp
            elif tool == "ask_human":
                operator_text = ask_answers.get(e.get("turn", -1), "")
                _transition("operator", "asking", src_t, {"text": operator_text})

        elif t == "tool_end":
            tool = e.get("tool", "")
            cfg  = TOOL_STATES.get(tool, {})

            if not cfg.get("terminal", False) and cfg.get("robot_state") is not None:
                _transition("robot", "standing", src_t)
                last_idle_src_t = src_t

            # Специфика
            if tool == "zoom":
                zoom_input = {}
            elif tool == "ask_human":
                _transition("operator", "idle", src_t)
                operator_text = ""
                last_idle_src_t = src_t

        elif t == "speech_start":
            speech_text = e.get("text", "")
            _transition("speech", "speaking", src_t, {"text": speech_text})
            # Если оператор спрашивал — теперь ждём пока дозвучит
            if open_intervals["operator"][1] == "asking":
                pass  # answered наступит на speech_end

        elif t == "speech_end":
            _transition("speech", "silent", src_t)
            if open_intervals["operator"][1] == "asking":
                _transition("operator", "answered", src_t,
                            {"text": operator_text})
            speech_text = ""

        elif t == "operator_message":
            # -i режим: оператор взял инициативу.
            # Начало отсчитываем с последнего idle-момента (api_response_end / tool_end),
            # а не с момента самого сообщения — оператор ждал с тех пор.
            text = e.get("text", "").strip()
            if text:
                start = last_idle_src_t if last_idle_src_t < src_t else src_t
                _flush("operator", start)
                open_intervals["operator"] = (start, "waiting_msg", {"text": text})

    # Закрыть все открытые интервалы по концу видео
    for actor in states:
        _flush(actor, duration)

    return result


# ── Derived states ────────────────────────────────────────────────────────────

def _add_derived_states(actors: dict[str, list[StateInterval]],
                        pre_zoom_sec: float = 1.0,
                        post_zoom_sec: float = 1.0) -> dict[str, list[StateInterval]]:
    """
    Вычисляет производные состояния поверх базовых интервалов.

    pre_zoom  — 1s до начала zooming: HUD гаснет.
    post_zoom — 1s после окончания zooming: HUD появляется.
    zooming остаётся реальной длительностью (~32ms от вызова инструмента).
    """
    robot = actors.get("robot", [])
    extra: list[StateInterval] = []

    for iv in robot:
        if iv.state == "zooming":
            pre_start = max(0.0, iv.src_start - pre_zoom_sec)
            extra.append(StateInterval(pre_start, iv.src_start,
                                       "pre_zoom", dict(iv.meta)))
            extra.append(StateInterval(iv.src_end, iv.src_end + post_zoom_sec,
                                       "post_zoom", dict(iv.meta)))

    if extra:
        actors = dict(actors)
        actors["robot"] = sorted(robot + extra, key=lambda iv: iv.src_start)

    return actors


# ── Точка входа ───────────────────────────────────────────────────────────────

def parse_session(session_dir: Path,
                  video_beep_t: float,
                  duration: float,
                  pre_zoom_sec: float = 1.0,
                  post_zoom_sec: float = 1.0) -> ParsedSession:
    """
    Главная функция Layer 1.

    video_beep_t — позиция бипа в видеофайле (секунды); определяется снаружи
                   через детекцию в аудио (Layer 0 / audio utils).
    duration     — длина видео в секундах.
    """
    raw_events   = load_events(session_dir)
    model        = _find_model(raw_events)
    beep_ts_wall = _find_beep(raw_events)
    if beep_ts_wall is None:
        raise ValueError(f"sync_beep не найден в {session_dir}/session.jsonl")

    events = annotate_src_t(raw_events, beep_ts_wall, video_beep_t)
    actors = _build_actor_intervals(events, duration)
    actors = _add_derived_states(actors, pre_zoom_sec, post_zoom_sec)

    return ParsedSession(
        session_dir  = session_dir,
        duration     = duration,
        beep_ts_wall = beep_ts_wall,
        video_beep_t = video_beep_t,
        events       = events,
        actors       = actors,
        model        = model,
    )
