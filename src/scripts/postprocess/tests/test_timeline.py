"""
Тесты для scripts/postprocess/timeline.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from scripts.postprocess.timeline import (
    build_timeline,
    build_pause_intervals,
    get_state_at,
)


def _make_event(ts: float, event_type: str, **kwargs) -> dict:
    return {"ts": ts, "type": event_type, **kwargs}


class TestBuildTimeline:
    def test_vt_formula(self):
        """vt = (ts_wall - beep_ts_wall) + video_beep_t"""
        beep_ts_wall = 1000.0
        video_beep_t = 5.0

        events = [
            _make_event(ts=1000.0, event_type="session_start"),
            _make_event(ts=1010.0, event_type="api_request_start"),
            _make_event(ts=1015.0, event_type="api_response_end"),
        ]

        tl = build_timeline(events, beep_ts_wall, video_beep_t)

        assert tl[0]["vt"] == pytest.approx(5.0)   # (1000-1000)+5 = 5
        assert tl[1]["vt"] == pytest.approx(15.0)  # (1010-1000)+5 = 15
        assert tl[2]["vt"] == pytest.approx(20.0)  # (1015-1000)+5 = 20

    def test_original_event_fields_preserved(self):
        """build_timeline не теряет поля исходных событий."""
        events = [_make_event(ts=100.0, event_type="tool_start", tool="move", turn=1)]
        tl = build_timeline(events, beep_ts_wall=100.0, video_beep_t=0.0)
        assert tl[0]["tool"] == "move"
        assert tl[0]["turn"] == 1
        assert "vt" in tl[0]


class TestBuildPauseIntervals:
    def test_basic_pause(self):
        """Одна пара api_request/api_response → одна пауза."""
        beep_ts_wall = 1000.0
        video_beep_t = 0.0
        events = [
            _make_event(ts=1002.0, event_type="api_request_start"),
            _make_event(ts=1007.0, event_type="api_response_end"),
        ]
        tl = build_timeline(events, beep_ts_wall, video_beep_t)
        pauses = build_pause_intervals(tl, video_duration=60.0)

        assert len(pauses) == 1
        s, e = pauses[0]
        assert s == pytest.approx(2.0)
        assert e == pytest.approx(7.0)

    def test_multiple_pauses(self):
        """Несколько пар api_request/api_response → несколько пауз."""
        beep_ts_wall = 1000.0
        video_beep_t = 0.0
        events = [
            _make_event(ts=1001.0, event_type="api_request_start"),
            _make_event(ts=1004.0, event_type="api_response_end"),
            _make_event(ts=1010.0, event_type="api_request_start"),
            _make_event(ts=1015.0, event_type="api_response_end"),
        ]
        tl = build_timeline(events, beep_ts_wall, video_beep_t)
        pauses = build_pause_intervals(tl, video_duration=60.0)

        assert len(pauses) == 2
        assert pauses[0] == pytest.approx((1.0, 4.0))
        assert pauses[1] == pytest.approx((10.0, 15.0))

    def test_pause_clamped_to_video_duration(self):
        """Конец паузы обрезается до video_duration."""
        beep_ts_wall = 1000.0
        video_beep_t = 0.0
        events = [
            _make_event(ts=1050.0, event_type="api_request_start"),
            _make_event(ts=1080.0, event_type="api_response_end"),
        ]
        tl = build_timeline(events, beep_ts_wall, video_beep_t)
        pauses = build_pause_intervals(tl, video_duration=60.0)

        assert len(pauses) == 1
        assert pauses[0][1] == pytest.approx(60.0)

    def test_no_events(self):
        """Нет событий → нет пауз."""
        pauses = build_pause_intervals([], video_duration=60.0)
        assert pauses == []


class TestGetStateAt:
    def _make_timeline(self, beep_ts_wall=1000.0, video_beep_t=0.0, events=None):
        from scripts.postprocess.timeline import build_timeline
        return build_timeline(events or [], beep_ts_wall, video_beep_t)

    def test_thinking_between_request_and_response(self):
        """Между api_request_start и api_response_end action == 'thinking'."""
        events = [
            _make_event(ts=1001.0, event_type="api_request_start"),
            _make_event(ts=1005.0, event_type="api_response_end"),
        ]
        tl = build_timeline(events, beep_ts_wall=1000.0, video_beep_t=0.0)

        action, subtitle = get_state_at(tl, vt=2.0)  # между 1.0 и 5.0
        assert action == "thinking"
        assert subtitle == ""

    def test_idle_after_response(self):
        """После api_response_end action == 'idle'."""
        events = [
            _make_event(ts=1001.0, event_type="api_request_start"),
            _make_event(ts=1005.0, event_type="api_response_end"),
        ]
        tl = build_timeline(events, beep_ts_wall=1000.0, video_beep_t=0.0)

        action, subtitle = get_state_at(tl, vt=6.0)
        assert action == "idle"

    def test_moving_after_tool_start_move(self):
        """После tool_start(move) action == 'move'."""
        events = [
            _make_event(ts=1001.0, event_type="tool_start", tool="move", turn=1),
        ]
        tl = build_timeline(events, beep_ts_wall=1000.0, video_beep_t=0.0)

        action, subtitle = get_state_at(tl, vt=5.0)
        assert action == "move"

    def test_turn_after_tool_start_turn_relative(self):
        """После tool_start(turn_relative) action == 'turn'."""
        events = [
            _make_event(ts=1001.0, event_type="tool_start",
                        tool="turn_relative", turn=1),
        ]
        tl = build_timeline(events, beep_ts_wall=1000.0, video_beep_t=0.0)

        action, subtitle = get_state_at(tl, vt=5.0)
        assert action == "turn"

    def test_speaking_with_subtitle(self):
        """speech_start устанавливает action='speaking' и subtitle."""
        events = [
            _make_event(ts=1002.0, event_type="speech_start", text="Привет мир"),
        ]
        tl = build_timeline(events, beep_ts_wall=1000.0, video_beep_t=0.0)

        action, subtitle = get_state_at(tl, vt=5.0)
        assert action == "speaking"
        assert subtitle == "Привет мир"

    def test_idle_before_any_event(self):
        """До первого события action == 'idle'."""
        events = [
            _make_event(ts=1010.0, event_type="api_request_start"),
        ]
        tl = build_timeline(events, beep_ts_wall=1000.0, video_beep_t=0.0)

        action, subtitle = get_state_at(tl, vt=5.0)  # раньше 10.0
        assert action == "idle"
