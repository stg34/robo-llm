"""Вспомогательные фабрики для построения тестовых данных без session.jsonl."""
from pathlib import Path
from ..parser import ParsedSession, StateInterval


def make_session(
    actors: dict[str, list[StateInterval]],
    duration: float = 100.0,
) -> ParsedSession:
    return ParsedSession(
        session_dir  = Path("/fake/session"),
        duration     = duration,
        beep_ts_wall = 0.0,
        video_beep_t = 0.0,
        events       = [],
        actors       = actors,
        model        = "",
    )


def ivs(*args) -> list[StateInterval]:
    """Быстрое создание списка StateInterval: ivs((start, end, state), ...)"""
    return [StateInterval(src_start=s, src_end=e, state=st, meta=m)
            for s, e, st, m in
            [(a[0], a[1], a[2], a[3] if len(a) > 3 else {}) for a in args]]
