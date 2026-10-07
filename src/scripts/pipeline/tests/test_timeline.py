"""Тесты Layer 3a: build_timeline."""
import pytest
from ..parser import StateInterval
from ..rules import Cut, Insert, Filter, MixAudio
from ..timeline import build_timeline, VideoSegment, SynthSegment, ZOOM_ANIM_DURATION
from .helpers import make_session


def _session(duration=100.0):
    actors = {
        "ai":       [StateInterval(0, duration, "idle")],
        "robot":    [StateInterval(0, duration, "standing")],
        "speech":   [StateInterval(0, duration, "silent")],
        "operator": [StateInterval(0, duration, "idle")],
    }
    return make_session(actors, duration)


# ── apply_cuts=False: только вставки ─────────────────────────────────────────

def test_no_cuts_all_video_preserved():
    """apply_cuts=False — вся длительность оригинала + вставки."""
    session = _session(60.0)
    ops = [
        Cut(10, 20),
        Insert(30.0, "zoom_animation", anchor="after_next_cut"),
    ]
    tl = build_timeline(session, ops, apply_cuts=False)
    video_dur = sum(s.duration for s in tl.segments if isinstance(s, VideoSegment))
    assert video_dur == pytest.approx(60.0)


def test_no_cuts_inserts_placed_at_src_t():
    """apply_cuts=False — Insert(after_next_cut) трактуется как at."""
    session = _session(60.0)
    ops = [Insert(30.0, "zoom_animation", anchor="after_next_cut")]
    tl = build_timeline(session, ops, apply_cuts=False)
    synths = [s for s in tl.segments if isinstance(s, SynthSegment)]
    assert len(synths) == 1
    assert synths[0].src_t == pytest.approx(30.0)


def test_no_cuts_total_duration():
    """apply_cuts=False — total = оригинал + N×zoom_anim."""
    session = _session(60.0)
    ops = [
        Insert(20.0, "zoom_animation", anchor="at"),
        Insert(40.0, "zoom_animation", anchor="at"),
    ]
    tl = build_timeline(session, ops, apply_cuts=False)
    assert tl.total_duration == pytest.approx(60.0 + 2 * ZOOM_ANIM_DURATION)


# ── apply_cuts=True: вырезание ────────────────────────────────────────────────

def test_cut_removes_interval():
    """Cut [10, 20] — эти 10s пропадают из итогового видео."""
    session = _session(60.0)
    ops = [Cut(10, 20)]
    tl = build_timeline(session, ops, apply_cuts=True)
    video_dur = sum(s.duration for s in tl.segments if isinstance(s, VideoSegment))
    assert video_dur == pytest.approx(50.0)
    assert tl.total_duration == pytest.approx(50.0)


def test_multiple_cuts():
    """Два Cut-а — суммарно вырезанное время вычтено."""
    session = _session(100.0)
    ops = [Cut(10, 20), Cut(40, 55)]
    tl = build_timeline(session, ops, apply_cuts=True)
    assert tl.total_duration == pytest.approx(75.0)


def test_insert_after_next_cut_placed_after_cut():
    """Insert(after_next_cut) вставляется на месте Cut, не в src_t."""
    session = _session(60.0)
    ops = [
        Cut(20, 30),
        Insert(15.0, "zoom_animation", anchor="after_next_cut"),
    ]
    tl = build_timeline(session, ops, apply_cuts=True)
    synths = [s for s in tl.segments if isinstance(s, SynthSegment)]
    assert len(synths) == 1
    # SynthSegment должен быть там где был Cut (out_start ≈ 20)
    assert synths[0].out_start == pytest.approx(20.0)


def test_final_total_duration():
    """Финальное видео: оригинал − cuts + inserts."""
    session = _session(100.0)
    ops = [
        Cut(10, 25),   # −15s
        Cut(50, 60),   # −10s
        Insert(9.0, "zoom_animation", anchor="after_next_cut"),  # +3.2s
    ]
    tl = build_timeline(session, ops, apply_cuts=True)
    expected = 100.0 - 15.0 - 10.0 + ZOOM_ANIM_DURATION
    assert tl.total_duration == pytest.approx(expected)


# ── prefix sum / out_start ────────────────────────────────────────────────────

def test_prefix_sum_contiguous():
    """out_start каждого сегмента = out_end предыдущего."""
    session = _session(60.0)
    ops = [Cut(10, 20), Insert(5.0, "zoom_animation", anchor="at")]
    tl = build_timeline(session, ops, apply_cuts=True)
    for i in range(1, len(tl.segments)):
        assert tl.segments[i].out_start == pytest.approx(tl.segments[i-1].out_end)


def test_first_segment_out_start_is_zero():
    session = _session(60.0)
    tl = build_timeline(session, [Cut(20, 30)], apply_cuts=True)
    assert tl.segments[0].out_start == pytest.approx(0.0)


# ── src_to_out / out_to_src ───────────────────────────────────────────────────

def test_src_to_out_basic():
    """src_t перед cut маппится 1:1."""
    session = _session(60.0)
    tl = build_timeline(session, [Cut(20, 30)], apply_cuts=True)
    assert tl.src_to_out(10.0) == pytest.approx(10.0)


def test_src_to_out_after_cut_shifted():
    """src_t после cut сдвигается на длину cut."""
    session = _session(60.0)
    tl = build_timeline(session, [Cut(20, 30)], apply_cuts=True)
    assert tl.src_to_out(35.0) == pytest.approx(25.0)  # 35 − 10s cut


def test_src_to_out_in_cut_returns_none():
    """src_t внутри Cut → None."""
    session = _session(60.0)
    tl = build_timeline(session, [Cut(20, 30)], apply_cuts=True)
    assert tl.src_to_out(25.0) is None


def test_out_to_src_basic():
    session = _session(60.0)
    tl = build_timeline(session, [Cut(20, 30)], apply_cuts=True)
    assert tl.out_to_src(10.0) == pytest.approx(10.0)


def test_out_to_src_in_synth_returns_none():
    """out_t внутри SynthSegment → None."""
    session = _session(60.0)
    ops = [Insert(20.0, "zoom_animation", anchor="at")]
    tl = build_timeline(session, ops, apply_cuts=False)
    synth = next(s for s in tl.segments if isinstance(s, SynthSegment))
    mid = synth.out_start + synth.duration / 2
    assert tl.out_to_src(mid) is None


def test_src_to_out_clamp_in_cut():
    """src_t внутри Cut → clamp возвращает out_start следующего сегмента."""
    session = _session(60.0)
    tl = build_timeline(session, [Cut(20, 30)], apply_cuts=True)
    clamped = tl.src_to_out_clamp(25.0)
    assert clamped == pytest.approx(20.0)  # начало сегмента после cut


# ── out_filters: fade_hud_in после SynthSegment ───────────────────────────────

def test_fade_hud_in_generated_after_zoom_animation():
    """out_filter fade_hud_in должен начинаться ровно после SynthSegment.out_end."""
    from ..parser import _add_derived_states
    robot_base = [
        StateInterval(0,   9,     "standing"),
        StateInterval(9,   10,    "zooming", {"cx": 100, "cy": 200}),
        StateInterval(10,  100,   "standing"),
    ]
    actors_raw = {
        "ai":       [StateInterval(0, 100, "idle")],
        "robot":    robot_base,
        "speech":   [StateInterval(0, 100, "silent")],
        "operator": [StateInterval(0, 100, "idle")],
    }
    actors = _add_derived_states(actors_raw, pre_zoom_sec=1.0, post_zoom_sec=1.0)
    session = make_session(actors, 100.0)

    from ..rules import apply_rules
    ops = apply_rules(session)
    tl = build_timeline(session, ops, apply_cuts=False)

    synths = [s for s in tl.segments if isinstance(s, SynthSegment)]
    assert len(synths) == 1
    zoom_seg = synths[0]

    assert len(tl.out_filters) == 1
    effect, out_start, out_end = tl.out_filters[0]
    assert effect    == "fade_hud_in"
    assert out_start == pytest.approx(zoom_seg.out_end)
    assert out_end   > out_start  # длительность = post_zoom_sec
