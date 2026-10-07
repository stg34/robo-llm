"""Тесты Layer 1: _add_derived_states."""
import pytest
from ..parser import StateInterval, _add_derived_states


def _zoom_iv(start: float, end: float, cx=100, cy=200) -> StateInterval:
    return StateInterval(start, end, "zooming", {"cx": cx, "cy": cy})


def _robot(*intervals: StateInterval) -> dict:
    return {"robot": list(intervals)}


# ── zooming остаётся реальным ────────────────────────────────────────────────

def test_zooming_duration_unchanged():
    """zooming не растягивается — остаётся реальной длительностью из лога."""
    actors = _robot(_zoom_iv(10.0, 10.032))
    result = _add_derived_states(actors)
    zooms = [iv for iv in result["robot"] if iv.state == "zooming"]
    assert len(zooms) == 1
    assert zooms[0].src_start == pytest.approx(10.0)
    assert zooms[0].src_end   == pytest.approx(10.032)


# ── pre_zoom / post_zoom позиции ─────────────────────────────────────────────

def test_pre_zoom_starts_1s_before_zooming():
    actors = _robot(_zoom_iv(10.0, 10.032))
    result = _add_derived_states(actors)
    pre = [iv for iv in result["robot"] if iv.state == "pre_zoom"]
    assert len(pre) == 1
    assert pre[0].src_start == pytest.approx(9.0)
    assert pre[0].src_end   == pytest.approx(10.0)


def test_post_zoom_starts_at_real_zooming_end():
    """post_zoom начинается сразу после реального конца zooming."""
    actors = _robot(_zoom_iv(10.0, 10.032))
    result = _add_derived_states(actors)
    post = [iv for iv in result["robot"] if iv.state == "post_zoom"]
    assert len(post) == 1
    assert post[0].src_start == pytest.approx(10.032)
    assert post[0].src_end   == pytest.approx(11.032)


def test_pre_zoom_clamped_at_zero():
    """pre_zoom не уходит в отрицательное время."""
    actors = _robot(_zoom_iv(0.5, 0.532))
    result = _add_derived_states(actors)
    pre = [iv for iv in result["robot"] if iv.state == "pre_zoom"]
    assert pre[0].src_start == pytest.approx(0.0)


def test_meta_propagated_to_derived_states():
    """cx/cy из zooming копируются в pre_zoom и post_zoom."""
    actors = _robot(_zoom_iv(10.0, 10.032, cx=425, cy=360))
    result = _add_derived_states(actors)
    for state in ("pre_zoom", "post_zoom", "zooming"):
        iv = next(i for i in result["robot"] if i.state == state)
        assert iv.meta["cx"] == 425
        assert iv.meta["cy"] == 360


def test_multiple_zooms_independent():
    """Два зума — два независимых набора pre/zoom/post."""
    actors = _robot(_zoom_iv(10.0, 10.032), _zoom_iv(30.0, 30.032))
    result = _add_derived_states(actors)
    robot = result["robot"]
    pre  = [iv for iv in robot if iv.state == "pre_zoom"]
    post = [iv for iv in robot if iv.state == "post_zoom"]
    zoom = [iv for iv in robot if iv.state == "zooming"]
    assert len(pre)  == 2
    assert len(post) == 2
    assert len(zoom) == 2


def test_intervals_sorted_by_src_start():
    """Все интервалы отсортированы по src_start."""
    actors = _robot(_zoom_iv(10.0, 10.032), _zoom_iv(30.0, 30.032))
    result = _add_derived_states(actors)
    starts = [iv.src_start for iv in result["robot"]]
    assert starts == sorted(starts)
