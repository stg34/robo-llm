"""
Интеграционный тест на реальной сессии brain_20260513_191941.

Сессия содержит сложные случаи:
- Зум где ai=thinking стартует во время post_zoom (thinking.src_start < post_zoom.src_end)
- Два оператора waiting_msg в одной сессии
- Зум прямо в момент operator=waiting_msg (Cut начинается до post_zoom.src_end)

Фикстура: tests/fixtures/brain_20260513_191941/session.jsonl
Видеофайл не нужен — тестируем Layer 1+2+3a (parse→rules→timeline).
"""
from __future__ import annotations

import pytest
from pathlib import Path

from ..parser import parse_session
from ..rules import apply_rules, Cut, Filter, Overlay
from ..timeline import build_timeline, VideoSegment, SynthSegment
from ..hud_pipe import hud_alpha

FIXTURE = Path(__file__).parent / "fixtures" / "brain_20260513_191941"
BEEP_T   = 1.1
DURATION = 1154.26


# ── Фикстуры (module-scope: парсим один раз) ─────────────────────────────────

@pytest.fixture(scope="module")
def session():
    return parse_session(FIXTURE, video_beep_t=BEEP_T, duration=DURATION)


@pytest.fixture(scope="module")
def ops(session):
    return apply_rules(session)


@pytest.fixture(scope="module")
def timeline(session, ops):
    return build_timeline(session, ops, apply_cuts=True)


# ── Вспомогательные функции ───────────────────────────────────────────────────

def hold_hud_offs(timeline):
    return [f for f in timeline.filters if f.effect == "hold_hud_off"]


def video_segs(timeline):
    return [s for s in timeline.segments if isinstance(s, VideoSegment)]


def synth_segs(timeline):
    return [s for s in timeline.segments if isinstance(s, SynthSegment)]


# ── Тесты структуры Timeline ─────────────────────────────────────────────────

def test_timeline_has_video_and_synth_segments(timeline):
    assert video_segs(timeline), "нет VideoSegment"
    assert synth_segs(timeline), "нет SynthSegment (зумов)"


def test_timeline_segments_contiguous(timeline):
    """out_end каждого сегмента = out_start следующего."""
    segs = timeline.segments
    for a, b in zip(segs, segs[1:]):
        assert abs(a.out_end - b.out_start) < 1e-6, (
            f"разрыв между сегментами: {a.out_end:.3f} → {b.out_start:.3f}"
        )


def test_no_hold_hud_off_overlapping_video_segment(timeline):
    """hold_hud_off не должен перекрывать src-диапазон ни одного VideoSegment.

    Если перекрывается — HUD гаснет на реальных кадрах после zoom fade_in.
    Это главный класс багов в _next_cut_start_after.
    """
    for hoff in hold_hud_offs(timeline):
        for seg in video_segs(timeline):
            overlap = min(hoff.src_end, seg.src_end) - max(hoff.src_start, seg.src_start)
            assert overlap <= 0, (
                f"hold_hud_off [{hoff.src_start:.3f}–{hoff.src_end:.3f}] "
                f"перекрывает VideoSegment [{seg.src_start:.3f}–{seg.src_end:.3f}] "
                f"на {overlap:.3f}s"
            )


def test_fade_hud_in_count_equals_synth_count(timeline):
    """Каждый SynthSegment порождает ровно один fade_hud_in в out_filters."""
    fade_ins = [e for e in timeline.out_filters if e[0] == "fade_hud_in"]
    assert len(fade_ins) == len(synth_segs(timeline))


# ── Тесты alpha-кривой вокруг зумов ──────────────────────────────────────────

def _alpha_after_fade_in(timeline, synth_seg, fps=14.98) -> float:
    """Alpha сразу после окончания fade_hud_in для данного SynthSegment."""
    fade_in_end = synth_seg.out_end + 0.8  # FADE_IN_SEC
    # Первый кадр VideoSegment после зума
    next_vid = next(
        (s for s in video_segs(timeline) if s.out_start >= synth_seg.out_end - 1e-6),
        None,
    )
    if next_vid is None:
        return -1.0
    out_t = fade_in_end + 1.0 / fps  # один кадр после fade_in
    src_t = next_vid.src_start + (out_t - next_vid.out_start)
    return hud_alpha(timeline.filters, timeline.out_filters, src_t, out_t)


def test_hud_alpha_after_each_zoom_fade_in(timeline):
    """После fade_hud_in каждого зума alpha должна быть 1.0.

    Нарушается когда hold_hud_off или fade_hud_out перекрывает начало
    следующего VideoSegment.
    """
    for i, synth in enumerate(synth_segs(timeline)):
        alpha = _alpha_after_fade_in(timeline, synth)
        assert abs(alpha - 1.0) < 1e-6, (
            f"zoom #{i+1} (out={synth.out_start:.1f}–{synth.out_end:.1f}): "
            f"alpha после fade_in = {alpha:.3f}, ожидали 1.0"
        )


# ── Тесты правил для operator=waiting_msg ────────────────────────────────────

def test_operator_waiting_msg_overlay_at_end(ops, session):
    """Overlay(operator_initiative) начинается в src_end - display_dur, не в src_start."""
    from ..rules import _operator_display_dur
    overlays = [op for op in ops if isinstance(op, Overlay) and op.kind == "operator_initiative"]
    assert overlays, "нет оверлеев operator_initiative в сессии"

    for op_iv in session.actors.get("operator", []):
        if op_iv.state != "waiting_msg":
            continue
        text = op_iv.meta.get("text", "")
        orig_dur = op_iv.src_end - op_iv.src_start
        display_dur = _operator_display_dur(text, orig_dur)
        expected_start = op_iv.src_end - display_dur

        matching = [o for o in overlays if abs(o.src_start - expected_start) < 0.1]
        assert matching, (
            f"waiting_msg [{op_iv.src_start:.3f}–{op_iv.src_end:.3f}]: "
            f"Overlay должен начинаться в {expected_start:.3f}, "
            f"найдены: {[o.src_start for o in overlays]}"
        )
        ov = matching[0]
        assert abs(ov.src_end - op_iv.src_end) < 0.1, (
            f"Overlay.src_end={ov.src_end:.3f} ≠ waiting_msg.src_end={op_iv.src_end:.3f}"
        )


def test_operator_waiting_msg_cut_at_head(ops, session):
    """Cut для waiting_msg: срезает голову (src_start..overlay_start), не хвост."""
    from ..rules import _operator_display_dur
    cuts = [op for op in ops if isinstance(op, Cut)]

    for op_iv in session.actors.get("operator", []):
        if op_iv.state != "waiting_msg":
            continue
        text = op_iv.meta.get("text", "")
        orig_dur = op_iv.src_end - op_iv.src_start
        display_dur = _operator_display_dur(text, orig_dur)
        overlay_start = op_iv.src_end - display_dur

        if overlay_start > op_iv.src_start + 0.1:
            matching_cut = next(
                (c for c in cuts if abs(c.src_start - op_iv.src_start) < 0.1
                 and abs(c.src_end - overlay_start) < 0.1),
                None,
            )
            assert matching_cut is not None, (
                f"waiting_msg [{op_iv.src_start:.3f}–{op_iv.src_end:.3f}]: "
                f"Cut(src_start={op_iv.src_start:.3f}, "
                f"overlay_start={overlay_start:.3f}) не найден"
            )


def test_operator_answered_overlay_at_end(ops, session):
    """Overlay(operator_answer) начинается в src_end - display_dur, не в src_start."""
    from ..rules import _operator_display_dur
    overlays = [op for op in ops if isinstance(op, Overlay) and op.kind == "operator_answer"]
    assert overlays, "нет оверлеев operator_answer в сессии"

    for op_iv in session.actors.get("operator", []):
        if op_iv.state != "answered":
            continue
        text = op_iv.meta.get("text", "")
        orig_dur = op_iv.src_end - op_iv.src_start
        display_dur = _operator_display_dur(text, orig_dur)
        expected_start = op_iv.src_end - display_dur

        matching = [o for o in overlays if abs(o.src_start - expected_start) < 0.1]
        assert matching, (
            f"answered [{op_iv.src_start:.3f}–{op_iv.src_end:.3f}]: "
            f"Overlay должен начинаться в {expected_start:.3f}, "
            f"найдены: {[o.src_start for o in overlays]}"
        )
        ov = matching[0]
        assert abs(ov.src_end - op_iv.src_end) < 0.1, (
            f"Overlay.src_end={ov.src_end:.3f} ≠ answered.src_end={op_iv.src_end:.3f}"
        )


def test_operator_answered_no_cut(ops, session):
    """answered не вырезает голову: робот может делать что-то интересное пока оператор печатает."""
    from ..rules import _operator_display_dur
    cuts = [op for op in ops if isinstance(op, Cut)]

    for op_iv in session.actors.get("operator", []):
        if op_iv.state != "answered":
            continue
        text = op_iv.meta.get("text", "")
        orig_dur = op_iv.src_end - op_iv.src_start
        display_dur = _operator_display_dur(text, orig_dur)
        overlay_start = op_iv.src_end - display_dur

        spurious_cut = next(
            (c for c in cuts if abs(c.src_start - op_iv.src_start) < 0.1
             and abs(c.src_end - overlay_start) < 0.1),
            None,
        )
        assert spurious_cut is None, (
            f"answered [{op_iv.src_start:.3f}–{op_iv.src_end:.3f}]: "
            f"не должно быть Cut на голове, но найден {spurious_cut}"
        )


# ── Числовые проверки для конкретных событий ─────────────────────────────────

def test_first_zoom_cut_positions(ops):
    """Первый зум: Cut начинается в post_zoom.src_end≈77.703.

    Thinking стартует во время post_zoom — cut_thinking использует
    post_zoom.src_end как cut_start, а не thinking.src_start.
    """
    cuts = [op for op in ops if isinstance(op, Cut)]
    zoom_cut = next((c for c in cuts if 77.0 < c.src_start < 78.5), None)
    assert zoom_cut is not None, "Cut около src=77.7 не найден"
    assert 77.5 < zoom_cut.src_start < 78.0, (
        f"Cut первого зума: src_start={zoom_cut.src_start:.3f}, ожидали ~77.703"
    )
    assert zoom_cut.src_end > zoom_cut.src_start + 1.0, (
        f"Cut первого зума слишком короткий: {zoom_cut.src_end - zoom_cut.src_start:.3f}s"
    )


def test_second_waiting_msg_generates_overlay(ops):
    """Второй waiting_msg (src≈766.730) порождает Overlay."""
    overlays = [op for op in ops if isinstance(op, Overlay) and op.kind == "operator_initiative"]
    # Должен быть оверлей около src=781-785
    late_overlays = [o for o in overlays if o.src_start > 770]
    assert late_overlays, "нет Overlay(operator_initiative) для второго waiting_msg (src≈766)"
