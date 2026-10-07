"""
Загрузка данных сессии и функции работы с timeline событий.
"""
import json
from pathlib import Path


def load_telemetry(session_dir: Path) -> list[dict]:
    path = session_dir / "telemetry.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: r["ts"])
    acc = 0.0
    prev_ts = None
    for rec in rows:
        if prev_ts is not None:
            acc += rec.get("gyro_z", 0.0) * (rec["ts"] - prev_ts)
        rec["gyro_angle"] = acc
        prev_ts = rec["ts"]
    return rows


def load_events(session_dir: Path) -> list[dict]:
    path = session_dir / "session.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: r["ts"])
    return rows


def build_timeline(events: list[dict], beep_ts_wall: float,
                   video_beep_t: float) -> list[dict]:
    """Перевести события из wall-clock в видео-время.

    video_t = (ts_wall - beep_ts_wall) + video_beep_t
    """
    def to_vt(ts_wall: float) -> float:
        return (ts_wall - beep_ts_wall) + video_beep_t

    return [{**e, "vt": to_vt(e["ts"])} for e in events]


def build_pause_intervals(timeline: list[dict],
                          video_duration: float) -> list[tuple]:
    """Интервалы пауз (thinking): api_request_start → api_response_end.

    Возвращает список (start_vt, end_vt) для вырезания.
    """
    pauses = []
    start = None
    for e in timeline:
        if e["type"] == "api_request_start":
            start = e["vt"]
        elif e["type"] == "api_response_end" and start is not None:
            if e["vt"] > start and start >= 0:
                pauses.append((max(0, start), min(e["vt"], video_duration)))
            start = None
    return pauses


def get_state_at(timeline: list[dict], vt: float) -> tuple[str, str]:
    """Вернуть (action, subtitle) для момента vt."""
    action = "idle"
    subtitle = ""

    for e in timeline:
        if e["vt"] > vt:
            break
        t = e["type"]
        if t == "api_request_start":
            action = "thinking"
            subtitle = ""
        elif t == "api_response_end":
            action = "idle"
        elif t == "speech_start":
            action = "speaking"
            subtitle = e.get("text", "")
        elif t == "speech_end":
            action = "idle"
            subtitle = ""
        elif t == "tool_start":
            tool = e.get("tool", "")
            if tool == "move":
                action = "move"
            elif tool in ("turn_relative", "turn_absolute"):
                action = "turn"
            elif tool == "done":
                action = "done"
            else:
                action = "idle"
        elif t == "tool_end":
            action = "idle"

    return action, subtitle


def get_operator_overlay_at(timeline: list[dict], vt: float) -> tuple[str, str] | None:
    """Вернуть (text, title) оверлея оператора для момента vt, или None.

    Два случая:
    1. ask_human (инициатива робота): интервал tool_start→tool_end.
       Заголовок: "ОТВЕТ ОПЕРАТОРА".
    2. operator_message (инициатива оператора, -i режим): idle-период до события.
       Заголовок: "ОПЕРАТОР".
    """
    intervals: list[tuple[float, float, str, str]] = []

    # --- Случай 1: ответ на ask_human ---
    # Показываем ответ начиная с speech_end (робот закончил говорить вопрос),
    # а не с tool_start (иначе ответ появляется одновременно с вопросом).
    ask_starts: dict[int, float] = {}   # turn → tool_start vt
    speech_end_after: dict[int, float] = {}  # turn → последний speech_end после tool_start
    pending_turn: int | None = None
    for e in timeline:
        t = e["type"]
        if t == "tool_start" and e.get("tool") == "ask_human":
            pending_turn = e.get("turn", -1)
            ask_starts[pending_turn] = e["vt"]
        elif t == "speech_end" and pending_turn is not None:
            speech_end_after[pending_turn] = e["vt"]
        elif t == "tool_end" and e.get("tool") == "ask_human":
            turn = e.get("turn", -1)
            if turn in ask_starts:
                answer = (e.get("result") or {}).get("answer", "").strip()
                if answer and answer != "(нет ответа)":
                    show_from = speech_end_after.get(turn, ask_starts[turn])
                    intervals.append((show_from, e["vt"], answer, "ОТВЕТ ОПЕРАТОРА"))
            pending_turn = None

    # --- Случай 2: сообщение оператора (-i режим) ---
    last_active_vt = None
    has_request = False
    for e in timeline:
        t = e["type"]
        if t in ("tool_end", "api_response_end"):
            last_active_vt = e["vt"]
            has_request = False
        elif t == "api_request_start":
            has_request = True
        elif t == "operator_message" and last_active_vt is not None and not has_request:
            text = e.get("text", "").strip()
            if text:
                intervals.append((last_active_vt, e["vt"], text, "ОПЕРАТОР"))

    for start, end, text, title in intervals:
        if start <= vt <= end:
            return text, title
    return None


def find_telemetry(telem: list[dict], ts_wall: float) -> dict | None:
    """Бинарный поиск ближайшего пакета телеметрии по wall-clock времени."""
    if not telem:
        return None
    lo, hi = 0, len(telem) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if telem[mid]["ts"] < ts_wall:
            lo = mid + 1
        else:
            hi = mid
    if lo > 0 and abs(telem[lo - 1]["ts"] - ts_wall) < abs(telem[lo]["ts"] - ts_wall):
        return telem[lo - 1]
    return telem[lo]


def get_turn_at(timeline: list[dict], vt: float) -> int:
    """Номер витка на момент vt: количество api_request_start до vt включительно."""
    n = 0
    for e in timeline:
        if e["vt"] > vt:
            break
        if e["type"] == "api_request_start":
            n += 1
    return n


def get_tokens_at(timeline: list[dict], vt: float) -> int | None:
    """Накопленные токены (input+output) по всем api_response_end до момента vt."""
    total = None
    for e in timeline:
        if e["vt"] > vt:
            break
        if e["type"] == "api_response_end" and "usage" in e:
            u = e["usage"]
            tokens = u.get("input_tokens", 0) + u.get("output_tokens", 0)
            total = (total or 0) + tokens
    return total
