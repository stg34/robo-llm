"""
Тесты для scripts/postprocess/zoom.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from scripts.postprocess.zoom import build_zoom_pause_map


def _make_event(ts: float, vt: float, event_type: str, **kwargs) -> dict:
    return {"ts": ts, "vt": vt, "type": event_type, **kwargs}


class TestBuildZoomPauseMap:
    def _make_zoom_pair(self, vt_start: float, vt_end: float, turn: int,
                        cx: int = 100, cy: int = 200) -> list[dict]:
        """Вспомогательная функция: пара tool_start/tool_end для zoom."""
        return [
            _make_event(ts=0, vt=vt_start, event_type="tool_start",
                        tool="zoom", turn=turn, input={"cx": cx, "cy": cy}),
            _make_event(ts=0, vt=vt_end,   event_type="tool_end",
                        tool="zoom", turn=turn),
        ]

    def test_basic_zoom_before_pause(self):
        """Zoom tool_end непосредственно перед паузой → матчится с этой паузой."""
        timeline = self._make_zoom_pair(vt_start=5.0, vt_end=6.0, turn=1,
                                        cx=320, cy=240)
        pauses = [(6.0, 12.0), (20.0, 25.0)]

        result = build_zoom_pause_map(timeline, pauses)

        assert len(result) == 1
        assert 0 in result   # первая пауза
        assert result[0]["cx_ai"] == 320
        assert result[0]["cy_ai"] == 240
        assert result[0]["zoom_num"] == 1

    def test_zoom_with_gap_before_pause(self):
        """Zoom tool_end задолго до паузы (через operator_message) → тоже матчится.

        Это был баг — исправлен убором жёсткого порога по времени. Ищем
        ближайшую паузу, начинающуюся ПОСЛЕ tool_end, без ограничения дельты.
        """
        # zoom заканчивается в 6.0, потом идёт operator_message (не в timeline),
        # потом пауза в 15.0
        timeline = self._make_zoom_pair(vt_start=5.0, vt_end=6.0, turn=1,
                                        cx=400, cy=300)
        pauses = [(15.0, 20.0)]

        result = build_zoom_pause_map(timeline, pauses)

        assert len(result) == 1
        assert 0 in result
        assert result[0]["zoom_num"] == 1

    def test_no_zoom_events(self):
        """Нет zoom событий → пустой словарь."""
        timeline = [
            _make_event(ts=0, vt=1.0, event_type="api_request_start"),
            _make_event(ts=0, vt=5.0, event_type="api_response_end"),
            _make_event(ts=0, vt=8.0, event_type="tool_start",
                        tool="move", turn=1, input={"meters": 1.0}),
        ]
        pauses = [(1.0, 5.0)]

        result = build_zoom_pause_map(timeline, pauses)

        assert result == {}

    def test_two_zooms_two_pauses(self):
        """Два zoom → каждый матчится со своей паузой."""
        timeline = (
            self._make_zoom_pair(vt_start=2.0, vt_end=3.0, turn=1, cx=100, cy=100) +
            self._make_zoom_pair(vt_start=15.0, vt_end=16.0, turn=2, cx=200, cy=200)
        )
        pauses = [(3.0, 8.0), (16.0, 22.0)]

        result = build_zoom_pause_map(timeline, pauses)

        assert len(result) == 2
        assert result[0]["zoom_num"] == 1
        assert result[1]["zoom_num"] == 2

    def test_pause_before_zoom_not_matched(self):
        """Пауза, которая начинается ДО zoom tool_end, не матчится с этим zoom."""
        timeline = self._make_zoom_pair(vt_start=10.0, vt_end=11.0, turn=1,
                                        cx=100, cy=100)
        # Пауза начинается в 5.0 — раньше vt_end zoom=11.0
        pauses = [(5.0, 9.0)]

        result = build_zoom_pause_map(timeline, pauses)

        assert result == {}
