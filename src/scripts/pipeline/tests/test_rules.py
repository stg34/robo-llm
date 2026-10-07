"""Тесты Layer 2: правила rule engine."""
import pytest
from ..parser import StateInterval
from ..rules import Cut, Insert, Filter, StateQuery, apply_rules
from .helpers import make_session, ivs


def _session_with_robot_ai(robot_states, ai_states, duration=100.0):
    actors = {
        "ai":       ai_states,
        "robot":    robot_states,
        "speech":   [StateInterval(0, duration, "silent")],
        "operator": [StateInterval(0, duration, "idle")],
    }
    return make_session(actors, duration)


# ── cut_thinking: базовый случай ─────────────────────────────────────────────

def test_cut_thinking_basic():
    """AI думает + robot стоит → Cut на весь интервал thinking."""
    session = _session_with_robot_ai(
        robot_states=[StateInterval(0, 100, "standing")],
        ai_states=[
            StateInterval(0, 10, "idle"),
            StateInterval(10, 20, "thinking"),
            StateInterval(20, 100, "idle"),
        ],
    )
    ops = apply_rules(session)
    cuts = [o for o in ops if isinstance(o, Cut)]
    assert len(cuts) == 1
    assert cuts[0].src_start == pytest.approx(10.0)
    assert cuts[0].src_end   == pytest.approx(20.0)


def test_cut_thinking_not_triggered_when_robot_moving():
    """Robot движется → cut_thinking не срабатывает."""
    session = _session_with_robot_ai(
        robot_states=[StateInterval(0, 100, "moving")],
        ai_states=[
            StateInterval(10, 20, "thinking"),
            StateInterval(20, 100, "idle"),
        ],
    )
    ops = apply_rules(session)
    cuts = [o for o in ops if isinstance(o, Cut)]
    assert len(cuts) == 0


# ── cut_thinking: не поглощает pre_zoom ──────────────────────────────────────

def test_cut_stops_before_pre_zoom():
    """Cut обрезается перед pre_zoom — HUD-окно не вырезается."""
    # thinking 10-25, pre_zoom 20-21 → Cut должен быть [10, 20]
    session = _session_with_robot_ai(
        robot_states=[
            StateInterval(0,  20, "standing"),
            StateInterval(20, 21, "pre_zoom"),
            StateInterval(21, 22, "zooming"),
            StateInterval(22, 23, "post_zoom"),
            StateInterval(23, 100, "standing"),
        ],
        ai_states=[
            StateInterval(10, 25, "thinking"),
            StateInterval(25, 100, "idle"),
        ],
    )
    ops = apply_rules(session)
    cuts = [o for o in ops if isinstance(o, Cut)]
    assert len(cuts) == 1
    assert cuts[0].src_end == pytest.approx(20.0)


def test_cut_stops_before_zooming():
    """Cut обрезается если zooming начинается внутри thinking."""
    session = _session_with_robot_ai(
        robot_states=[
            StateInterval(0,  15, "standing"),
            StateInterval(15, 16, "zooming"),
            StateInterval(16, 100, "standing"),
        ],
        ai_states=[
            StateInterval(10, 25, "thinking"),
            StateInterval(25, 100, "idle"),
        ],
    )
    ops = apply_rules(session)
    cuts = [o for o in ops if isinstance(o, Cut)]
    assert any(c.src_end == pytest.approx(15.0) for c in cuts)


# ── cut_thinking: откладывается при старте в post_zoom ───────────────────────

def test_cut_deferred_when_thinking_starts_in_post_zoom():
    """Thinking стартует в post_zoom → Cut начинается после конца post_zoom."""
    session = _session_with_robot_ai(
        robot_states=[
            StateInterval(0,  10, "standing"),
            StateInterval(10, 11, "zooming"),
            StateInterval(11, 13, "post_zoom"),
            StateInterval(13, 100, "standing"),
        ],
        ai_states=[
            StateInterval(11.5, 25, "thinking"),  # стартует в post_zoom
            StateInterval(25, 100, "idle"),
        ],
    )
    ops = apply_rules(session)
    cuts = [o for o in ops if isinstance(o, Cut)]
    assert len(cuts) == 1
    assert cuts[0].src_start == pytest.approx(13.0)  # после конца post_zoom


# ── zoom_animation insert ─────────────────────────────────────────────────────

def test_zoom_animation_insert_at_zoom_end():
    """robot=zooming → Insert(zoom_animation) с src_t = zooming.src_end."""
    session = _session_with_robot_ai(
        robot_states=[
            StateInterval(0,  10, "standing"),
            StateInterval(10, 11, "zooming", {"cx": 100, "cy": 200}),
            StateInterval(11, 100, "standing"),
        ],
        ai_states=[StateInterval(0, 100, "idle")],
    )
    ops = apply_rules(session)
    inserts = [o for o in ops if isinstance(o, Insert) and o.kind == "zoom_animation"]
    assert len(inserts) == 1
    assert inserts[0].src_t  == pytest.approx(11.0)
    assert inserts[0].anchor == "after_next_cut"


# ── StateQuery.state_at: дублирующиеся src_start ─────────────────────────────

def test_state_at_overlapping_start_times():
    """standing и post_zoom с одинаковым src_start — state_at не должен возвращать ''.

    Реальный кейс: post_zoom начинается в тот же момент что и standing (tool_end).
    bisect_right попадает на post_zoom; если он короче — надо найти standing позади.
    """
    actors = {
        "robot": [
            StateInterval(0,    10.0,  "standing"),
            StateInterval(10.0, 20.0,  "standing"),   # длинный
            StateInterval(10.0, 11.0,  "post_zoom"),  # короткий, тот же src_start
        ]
    }
    q = StateQuery({"robot": sorted(actors["robot"], key=lambda iv: iv.src_start)})
    # В середине standing, после конца post_zoom
    assert q.state_at("robot", 15.0) == "standing"
    # Внутри post_zoom
    assert q.state_at("robot", 10.5) == "post_zoom"


def test_cut_thinking_with_same_start_as_post_zoom():
    """cut_thinking срабатывает когда thinking начинается после post_zoom,
    но standing и post_zoom имеют одинаковый src_start (реальный баг)."""
    # standing 5–20, post_zoom 5–6, thinking 12–18
    # Без фикса state_at(robot, 12) = '' → cut не генерировался
    session = _session_with_robot_ai(
        robot_states=[
            StateInterval(0,  5,  "standing"),
            StateInterval(5,  20, "standing"),
            StateInterval(5,  6,  "post_zoom"),  # одинаковый src_start со standing
        ],
        ai_states=[
            StateInterval(12, 18, "thinking"),
            StateInterval(18, 100, "idle"),
        ],
    )
    ops = apply_rules(session)
    cuts = [o for o in ops if isinstance(o, Cut)]
    assert any(c.src_start == pytest.approx(12.0) and
               c.src_end   == pytest.approx(18.0)
               for c in cuts)


# ── pre_zoom_fade filter ──────────────────────────────────────────────────────

def test_pre_zoom_fade_filter_generated():
    """robot=pre_zoom → Filter(fade_hud_out) длиной FADE_OUT_SEC перед концом окна."""
    from ..rules import FADE_OUT_SEC
    session = _session_with_robot_ai(
        robot_states=[
            StateInterval(9,  10, "pre_zoom"),
            StateInterval(10, 11, "zooming"),
            StateInterval(11, 100, "standing"),
        ],
        ai_states=[StateInterval(0, 100, "idle")],
    )
    ops = apply_rules(session)
    filters = [o for o in ops if isinstance(o, Filter)]
    assert any(f.effect == "fade_hud_out" and
               f.src_end   == pytest.approx(10.0) and
               f.src_end - f.src_start == pytest.approx(FADE_OUT_SEC)
               for f in filters)
