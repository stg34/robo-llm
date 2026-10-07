"""
Visualizer — интерактивная Plotly HTML диаграмма тайминга сессии.

Показывает:
  - State intervals per actor (Layer 1) — цветные вертикальные полосы
  - Операции Layer 2 — Cut (красные зоны), Insert (зелёные линии),
    Filter (синие зоны), MixAudio (маркеры), Overlay (фиолетовые зоны)

Ось Y = время (src_t, сверху вниз), ось X = акторы.

Использование:
  python -m scripts.pipeline.visualize <session_dir> --beep-t 3.45
  python -m scripts.pipeline.visualize <session_dir> --beep-t 3.45 --duration 120
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import plotly.graph_objects as go

from .parser import ParsedSession, StateInterval
from .rules import Cut, Filter, Insert, MixAudio, Overlay, Operation
from .timeline import Timeline, VideoSegment, SynthSegment


# ── Цвета ─────────────────────────────────────────────────────────────────────

STATE_COLORS: dict[str, str] = {
    # AI
    "thinking":    "#e07b39",
    # Robot
    "moving":      "#d4b800",
    "turning":     "#a08800",
    "zooming":     "#00b4d8",
    "shooting":    "#e63946",
    "sleeping":    "#3a3a6e",
    "stopping":    "#ff6b35",
    "done":        "#888888",
    "dying":       "#6b0000",
    "pre_zoom":    "#006d8f",
    "post_zoom":   "#004a61",
    # Speech
    "speaking":    "#00c853",
    # Operator
    "asking":      "#7b2ff7",
    "answered":    "#9d4edd",
    "waiting_msg": "#c77dff",
}

# Фоновые состояния — не рисуем полосу, только фон строки
BACKGROUND_STATES = {"idle", "standing", "silent"}

ACTOR_LABEL = {
    "ai":       "AI",
    "robot":    "Robot",
    "speech":   "Speech",
    "operator": "Operator",
}

# Порядок колонок слева направо
ACTOR_ORDER = ["ai", "robot", "speech", "operator"]

SYNTH_COLORS: dict[str, str] = {
    "zoom_animation": "#ff8c00",
}

# ── Размеры фигур ─────────────────────────────────────────────────────────────
COL_WIDTH_PX  = 100   # ширина одной колонки (px)
COL_MARGIN_PX = 50   # суммарный горизонтальный отступ (px)


# ── Утилиты ──────────────────────────────────────────────────────────────────

def _map_intervals_to_out(
    intervals: list[StateInterval],
    segments: list[VideoSegment | SynthSegment],
) -> list[tuple[float, float, str, dict]]:
    """Проецирует StateInterval-ы из src_t в out_t через VideoSegment-ы.

    Интервалы, попавшие в Cut (нет VideoSegment), исчезают.
    Интервал, перекрывающий границу Cut, разбивается на части.
    """
    result = []
    video_segs = [s for s in segments if isinstance(s, VideoSegment)]
    for iv in intervals:
        for seg in video_segs:
            clip_start = max(iv.src_start, seg.src_start)
            clip_end   = min(iv.src_end,   seg.src_end)
            if clip_start < clip_end:
                out_start = seg.out_start + (clip_start - seg.src_start)
                out_end   = seg.out_start + (clip_end   - seg.src_start)
                result.append((out_start, out_end, iv.state, iv.meta))
    return result


def _wrap(text: str, width: int = 50) -> str:
    """Перенос текста по пробелам для Plotly tooltip (разделитель — <br>)."""
    words, lines, line = text.split(), [], ""
    for w in words:
        candidate = f"{line} {w}".strip()
        if len(candidate) > width and line:
            lines.append(line)
            line = w
        else:
            line = candidate
    if line:
        lines.append(line)
    return "<br>".join(lines)


# ── Построение фигуры ─────────────────────────────────────────────────────────

def build_figure(session: ParsedSession, ops: list[Operation],
                 timeline: Timeline | None = None) -> go.Figure:
    fig = go.Figure()
    seen_legend: set[str] = set()
    _speech_file: dict[float, str] = {
        op.src_t: op.path.name for op in ops if isinstance(op, MixAudio)
    }

    # ── Фоновые полосы колонок ────────────────────────────────────────────────
    for actor in ACTOR_ORDER:
        fig.add_trace(go.Bar(
            x=[ACTOR_LABEL[actor]],
            y=[session.duration],
            base=[0.0],
            marker=dict(color="rgba(40,40,40,0.25)"),
            showlegend=False,
            hoverinfo="skip",
        ))

    # ── State interval полосы ─────────────────────────────────────────────────
    for actor in ACTOR_ORDER:
        for iv in session.actors.get(actor, []):
            if iv.state in BACKGROUND_STATES:
                continue
            color = STATE_COLORS.get(iv.state, "#888888")
            label = iv.state
            show  = label not in seen_legend
            if show:
                seen_legend.add(label)

            hover = (
                f"<b>{ACTOR_LABEL[actor]} / {iv.state}</b><br>"
                f"src: {iv.src_start:.2f}s → {iv.src_end:.2f}s<br>"
                f"dur: {iv.duration:.2f}s"
            )
            if actor == "speech" and iv.state == "speaking":
                fname = min(_speech_file, key=lambda t: abs(t - iv.src_start),
                            default=None)
                if fname is not None and abs(fname - iv.src_start) < 0.5:
                    hover += f"<br>file: {_speech_file[fname]}"
            for k, v in iv.meta.items():
                hover += f"<br>{k}: {_wrap(str(v))}"

            fig.add_trace(go.Bar(
                name=label,
                x=[ACTOR_LABEL[actor]],
                y=[iv.duration],
                base=[iv.src_start],
                marker=dict(color=color),
                legendgroup=label,
                showlegend=show,
                hovertemplate=hover + "<extra></extra>",
            ))

    # ── Cut — красные полупрозрачные зоны ─────────────────────────────────────
    for op in ops:
        if not isinstance(op, Cut):
            continue
        fig.add_hrect(
            y0=op.src_start, y1=op.src_end,
            fillcolor="rgba(220,50,50,0.15)",
            line=dict(color="rgba(220,50,50,0.55)", width=1, dash="dot"),
            annotation_text="CUT",
            annotation_font=dict(size=10, color="rgba(220,50,50,0.8)"),
            annotation_position="top left",
        )

    # ── Insert — горизонтальные штриховые линии ───────────────────────────────
    for op in ops:
        if not isinstance(op, Insert):
            continue
        label = f"INSERT {op.kind}"
        if op.anchor == "after_next_cut":
            label += " (→ next cut)"
        fig.add_hline(
            y=op.src_t,
            line=dict(color="rgba(50,220,100,0.85)", width=2, dash="dash"),
            annotation_text=label,
            annotation_font=dict(size=9, color="rgba(50,220,100,1)"),
            annotation_position="top right",
        )

    # ── Filter — синие полупрозрачные зоны ───────────────────────────────────
    for op in ops:
        if not isinstance(op, Filter):
            continue
        fig.add_hrect(
            y0=op.src_start, y1=op.src_end,
            fillcolor="rgba(50,100,220,0.12)",
            line=dict(color="rgba(50,100,220,0.4)", width=1, dash="dot"),
            annotation_text=op.effect,
            annotation_font=dict(size=9, color="rgba(50,100,220,0.9)"),
            annotation_position="bottom left",
        )

    # ── MixAudio — маркеры на отдельной колонке ───────────────────────────────
    audio_ops = [op for op in ops if isinstance(op, MixAudio)]
    if audio_ops:
        fig.add_trace(go.Scatter(
            name="MixAudio",
            x=["Audio"] * len(audio_ops),
            y=[op.src_t for op in audio_ops],
            mode="markers",
            marker=dict(symbol="triangle-down", size=12,
                        color="rgba(0,200,80,0.9)"),
            hovertemplate=(
                "<b>MixAudio</b><br>"
                "src_t: %{y:.2f}s<br>"
                "%{customdata}<extra></extra>"
            ),
            customdata=[op.path.name + (f"<br>{op.meta.get('text','')[:40]}"
                                        if op.meta.get("text") else "")
                        for op in audio_ops],
            showlegend=True,
        ))

    # ── Overlay — фиолетовые зоны ─────────────────────────────────────────────
    for op in ops:
        if not isinstance(op, Overlay):
            continue
        fig.add_hrect(
            y0=op.src_start, y1=op.src_end,
            fillcolor="rgba(150,50,220,0.12)",
            line=dict(color="rgba(150,50,220,0.4)", width=1, dash="dot"),
            annotation_text=op.kind,
            annotation_font=dict(size=9, color="rgba(150,50,220,0.9)"),
            annotation_position="bottom right",
        )

    # ── Timeline segments ─────────────────────────────────────────────────────
    if timeline is not None:
        for seg in timeline.segments:
            if isinstance(seg, VideoSegment):
                hover = (f"<b>VideoSegment</b><br>"
                         f"src: {seg.src_start:.2f}s → {seg.src_end:.2f}s<br>"
                         f"out: {seg.out_start:.2f}s → {seg.out_end:.2f}s<br>"
                         f"dur: {seg.duration:.2f}s")
                fig.add_trace(go.Bar(
                    name="VideoSegment",
                    x=["Segments"],
                    y=[seg.duration],
                    base=[seg.src_start],
                    marker=dict(color="rgba(160,160,160,0.5)",
                                line=dict(color="rgba(200,200,200,0.8)", width=1)),
                    legendgroup="VideoSegment",
                    showlegend=seg is timeline.segments[0],
                    hovertemplate=hover + "<extra></extra>",
                ))
                fig.add_annotation(
                    x="Segments",
                    y=seg.src_start + seg.duration / 2,
                    text=f"{seg.out_start:.1f}s",
                    font=dict(size=8, color="rgba(200,200,200,0.7)"),
                    showarrow=False,
                )
            elif isinstance(seg, SynthSegment) and seg.src_t is not None:
                color = SYNTH_COLORS.get(seg.kind, "#ff8c00")
                hover = (f"<b>{seg.kind}</b><br>"
                         f"src_t: {seg.src_t:.2f}s (replaces cut)<br>"
                         f"out: {seg.out_start:.2f}s → {seg.out_end:.2f}s<br>"
                         f"dur: {seg.duration:.2f}s")
                if seg.meta:
                    for k, v in seg.meta.items():
                        hover += f"<br>{k}: {v}"
                fig.add_trace(go.Bar(
                    name=seg.kind,
                    x=["Segments"],
                    y=[seg.duration],
                    base=[seg.src_t],
                    marker=dict(color=color,
                                line=dict(color="rgba(255,200,0,0.9)", width=1)),
                    legendgroup=seg.kind,
                    showlegend=True,
                    hovertemplate=hover + "<extra></extra>",
                ))

    # ── Layout ────────────────────────────────────────────────────────────────
    audio_col    = ["Audio"] if audio_ops else []
    segment_col  = ["Segments"] if timeline is not None else []
    x_order      = [ACTOR_LABEL[a] for a in ACTOR_ORDER] + audio_col + segment_col

    fig.update_layout(
        title=dict(
            text=f"Session timeline — {session.session_dir.name}",
            font=dict(size=15),
        ),
        barmode="overlay",
        width=COL_MARGIN_PX + len(x_order) * COL_WIDTH_PX,
        height=max(800, int(session.duration * 10)),
        margin=dict(l=60, r=40, t=60, b=60),
        xaxis=dict(
            categoryorder="array",
            categoryarray=x_order,
            gridcolor="rgba(255,255,255,0.08)",
        ),
        yaxis=dict(
            title="src_t (seconds)",
            range=[session.duration, 0],   # время сверху вниз
            gridcolor="rgba(255,255,255,0.08)",
        ),
        plot_bgcolor="#111118",
        paper_bgcolor="#1a1a24",
        font=dict(color="#cccccc"),
        legend=dict(
            bgcolor="rgba(30,30,40,0.8)",
            bordercolor="rgba(255,255,255,0.15)",
            borderwidth=1,
        ),
        hoverlabel=dict(bgcolor="#222230", font_size=12),
    )

    return fig


def build_out_figure(session: ParsedSession, timeline: Timeline,
                     title_suffix: str = "") -> go.Figure:
    """Диаграмма результирующего видео в пространстве out_t."""
    fig = go.Figure()
    seen_legend: set[str] = set()
    total = timeline.total_duration
    _speech_file_out: dict[float, str] = {
        out_t: path.name for path, _src, out_t in timeline.audio_mixes
    }

    # ── Фоновые полосы колонок ────────────────────────────────────────────────
    for actor in ACTOR_ORDER:
        fig.add_trace(go.Bar(
            x=[ACTOR_LABEL[actor]],
            y=[total],
            base=[0.0],
            marker=dict(color="rgba(40,40,40,0.25)"),
            showlegend=False,
            hoverinfo="skip",
        ))

    # ── Состояния акторов в out_t ─────────────────────────────────────────────
    for actor in ACTOR_ORDER:
        source = [iv for iv in session.actors.get(actor, [])
                  if iv.state not in BACKGROUND_STATES]
        mapped = _map_intervals_to_out(source, timeline.segments)
        for out_start, out_end, state, meta in mapped:
            dur   = out_end - out_start
            color = STATE_COLORS.get(state, "#888888")
            show  = state not in seen_legend
            if show:
                seen_legend.add(state)
            hover = (
                f"<b>{ACTOR_LABEL[actor]} / {state}</b><br>"
                f"out: {out_start:.2f}s → {out_end:.2f}s<br>"
                f"dur: {dur:.2f}s"
            )
            if actor == "speech" and state == "speaking":
                fname = min(_speech_file_out, key=lambda t: abs(t - out_start),
                            default=None)
                if fname is not None and abs(fname - out_start) < 0.5:
                    hover += f"<br>file: {_speech_file_out[fname]}"
            for k, v in meta.items():
                hover += f"<br>{k}: {_wrap(str(v))}"
            fig.add_trace(go.Bar(
                name=state,
                x=[ACTOR_LABEL[actor]],
                y=[dur],
                base=[out_start],
                marker=dict(color=color),
                legendgroup=state,
                showlegend=show,
                hovertemplate=hover + "<extra></extra>",
            ))

    # ── Segments column ───────────────────────────────────────────────────────
    for seg in timeline.segments:
        if isinstance(seg, VideoSegment):
            hover = (f"<b>VideoSegment</b><br>"
                     f"src: {seg.src_start:.2f}s → {seg.src_end:.2f}s<br>"
                     f"out: {seg.out_start:.2f}s → {seg.out_end:.2f}s<br>"
                     f"dur: {seg.duration:.2f}s")
            fig.add_trace(go.Bar(
                name="VideoSegment",
                x=["Segments"],
                y=[seg.duration],
                base=[seg.out_start],
                marker=dict(color="rgba(160,160,160,0.5)",
                            line=dict(color="rgba(200,200,200,0.8)", width=1)),
                legendgroup="VideoSegment",
                showlegend="VideoSegment" not in seen_legend,
                hovertemplate=hover + "<extra></extra>",
            ))
            seen_legend.add("VideoSegment")
        elif isinstance(seg, SynthSegment):
            color = SYNTH_COLORS.get(seg.kind, "#ff8c00")
            hover = (f"<b>{seg.kind}</b><br>"
                     f"out: {seg.out_start:.2f}s → {seg.out_end:.2f}s<br>"
                     f"dur: {seg.duration:.2f}s")
            for k, v in seg.meta.items():
                hover += f"<br>{k}: {v}"
            fig.add_trace(go.Bar(
                name=seg.kind,
                x=["Segments"],
                y=[seg.duration],
                base=[seg.out_start],
                marker=dict(color=color,
                            line=dict(color="rgba(255,200,0,0.9)", width=1)),
                legendgroup=seg.kind,
                showlegend=seg.kind not in seen_legend,
                hovertemplate=hover + "<extra></extra>",
            ))
            seen_legend.add(seg.kind)

    # ── MixAudio — уже в out_t ────────────────────────────────────────────────
    if timeline.audio_mixes:
        fig.add_trace(go.Scatter(
            name="MixAudio",
            x=["Audio"] * len(timeline.audio_mixes),
            y=[out_t for _, _src, out_t in timeline.audio_mixes],
            mode="markers",
            marker=dict(symbol="triangle-down", size=12,
                        color="rgba(0,200,80,0.9)"),
            hovertemplate=(
                "<b>MixAudio</b><br>"
                "out_t: %{y:.2f}s<br>"
                "%{customdata}<extra></extra>"
            ),
            customdata=[p.name for p, _src, _ in timeline.audio_mixes],
            showlegend=True,
        ))

    # ── Overlays — маппинг src_t → out_t ─────────────────────────────────────
    for ov in timeline.overlays:
        out_start = timeline.src_to_out_clamp(ov.src_start)
        out_end   = timeline.src_to_out_clamp(ov.src_end)
        if out_end > out_start:
            fig.add_hrect(
                y0=out_start, y1=out_end,
                fillcolor="rgba(150,50,220,0.12)",
                line=dict(color="rgba(150,50,220,0.4)", width=1, dash="dot"),
                annotation_text=ov.kind,
                annotation_font=dict(size=9, color="rgba(150,50,220,0.9)"),
                annotation_position="bottom right",
            )

    # ── Filters src_t → out_t (например fade_hud_out) ────────────────────────
    for fl in timeline.filters:
        out_start = timeline.src_to_out_clamp(fl.src_start)
        out_end   = timeline.src_to_out_clamp(fl.src_end)
        if out_end > out_start:
            fig.add_hrect(
                y0=out_start, y1=out_end,
                fillcolor="rgba(50,100,220,0.12)",
                line=dict(color="rgba(50,100,220,0.4)", width=1, dash="dot"),
                annotation_text=fl.effect,
                annotation_font=dict(size=9, color="rgba(50,100,220,0.9)"),
                annotation_position="bottom left",
            )

    # ── out_filters — уже в out_t (например fade_hud_in после анимации) ──────
    for effect, out_start, out_end in timeline.out_filters:
        fig.add_hrect(
            y0=out_start, y1=out_end,
            fillcolor="rgba(50,220,100,0.10)",
            line=dict(color="rgba(50,220,100,0.4)", width=1, dash="dot"),
            annotation_text=effect,
            annotation_font=dict(size=9, color="rgba(50,220,100,0.9)"),
            annotation_position="bottom right",
        )

    # ── Layout ────────────────────────────────────────────────────────────────
    audio_col = ["Audio"] if timeline.audio_mixes else []
    x_order   = [ACTOR_LABEL[a] for a in ACTOR_ORDER] + audio_col + ["Segments"]

    fig.update_layout(
        title=dict(
            text=f"Output timeline{title_suffix} — {session.session_dir.name}",
            font=dict(size=15),
        ),
        barmode="overlay",
        width=COL_MARGIN_PX + len(x_order) * COL_WIDTH_PX,
        height=max(600, int(total * 10)),
        margin=dict(l=60, r=40, t=60, b=60),
        xaxis=dict(
            categoryorder="array",
            categoryarray=x_order,
            gridcolor="rgba(255,255,255,0.08)",
        ),
        yaxis=dict(
            title="out_t (seconds)",
            range=[total, 0],
            gridcolor="rgba(255,255,255,0.08)",
        ),
        plot_bgcolor="#111118",
        paper_bgcolor="#1a1a24",
        font=dict(color="#cccccc"),
        legend=dict(
            bgcolor="rgba(30,30,40,0.8)",
            bordercolor="rgba(255,255,255,0.15)",
            borderwidth=1,
        ),
        hoverlabel=dict(bgcolor="#222230", font_size=12),
    )

    return fig


def save_html(figures: list[go.Figure], path: Path) -> None:
    if len(figures) == 1:
        figures[0].write_html(str(path), include_plotlyjs="cdn")
    else:
        parts = []
        for i, fig in enumerate(figures):
            parts.append(fig.to_html(
                full_html=False,
                include_plotlyjs="cdn" if i == 0 else False,
            ))
        divider = "<hr style='border:1px solid #333;margin:0'>"
        content = (
            "<!DOCTYPE html><html>"
            "<head><meta charset='utf-8'>"
            f"<title>{path.stem}</title></head>"
            "<body style='background:#111118;margin:0'>"
            + divider.join(parts)
            + "</body></html>"
        )
        path.write_text(content, encoding="utf-8")
    print(f"Сохранено: {path}")


def save_json(session: ParsedSession, ops: list[Operation], path: Path) -> None:
    import json
    from dataclasses import asdict

    def _op_dict(op: Operation) -> dict:
        d = asdict(op)
        d["type"] = type(op).__name__
        if "path" in d:
            d["path"] = str(d["path"])
        return d

    data = {
        "session":    session.session_dir.name,
        "duration":   round(session.duration, 3),
        "beep_ts_wall": session.beep_ts_wall,
        "video_beep_t": round(session.video_beep_t, 3),
        "actors": {
            actor: [
                {
                    "state":     iv.state,
                    "src_start": round(iv.src_start, 3),
                    "src_end":   round(iv.src_end, 3),
                    "duration":  round(iv.duration, 3),
                    **({"meta": iv.meta} if iv.meta else {}),
                }
                for iv in ivs
            ]
            for actor, ivs in session.actors.items()
        },
        "operations": [_op_dict(op) for op in ops],
    }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Сохранено: {path}")


def save_timeline_json(session: ParsedSession, timeline: Timeline,
                       path: Path) -> None:
    """JSON с данными результирующего видео в out_t пространстве — для анализа."""
    import json

    segments = []
    for seg in timeline.segments:
        if isinstance(seg, VideoSegment):
            segments.append({
                "type":      "video",
                "src_start": round(seg.src_start, 3),
                "src_end":   round(seg.src_end,   3),
                "out_start": round(seg.out_start,  3),
                "out_end":   round(seg.out_end,    3),
                "duration":  round(seg.duration,   3),
            })
        elif isinstance(seg, SynthSegment):
            d = {
                "type":      "synth",
                "kind":      seg.kind,
                "out_start": round(seg.out_start, 3),
                "out_end":   round(seg.out_end,   3),
                "duration":  round(seg.duration,  3),
            }
            if seg.src_t is not None:
                d["src_t"] = round(seg.src_t, 3)
            if seg.meta:
                d["meta"] = seg.meta
            segments.append(d)

    actors_out: dict[str, list[dict]] = {}
    for actor in ACTOR_ORDER:
        source = [iv for iv in session.actors.get(actor, [])
                  if iv.state not in BACKGROUND_STATES]
        mapped = _map_intervals_to_out(source, timeline.segments)
        actors_out[actor] = [
            {
                "state":     state,
                "out_start": round(out_start, 3),
                "out_end":   round(out_end,   3),
                "duration":  round(out_end - out_start, 3),
                **({"meta": meta} if meta else {}),
            }
            for out_start, out_end, state, meta in mapped
        ]

    filters_out = []
    for fl in timeline.filters:
        out_s = timeline.src_to_out_clamp(fl.src_start)
        out_e = timeline.src_to_out_clamp(fl.src_end)
        filters_out.append({
            "effect":    fl.effect,
            "src_start": round(fl.src_start, 3),
            "src_end":   round(fl.src_end,   3),
            "out_start": round(out_s, 3),
            "out_end":   round(out_e, 3),
        })

    overlays_out = []
    for ov in timeline.overlays:
        out_s = timeline.src_to_out_clamp(ov.src_start)
        out_e = timeline.src_to_out_clamp(ov.src_end)
        overlays_out.append({
            "kind":      ov.kind,
            "src_start": round(ov.src_start, 3),
            "src_end":   round(ov.src_end,   3),
            "out_start": round(out_s, 3),
            "out_end":   round(out_e, 3),
            **({"meta": ov.meta} if ov.meta else {}),
        })

    audio_out = [
        {"file": p.name, "src_t": round(src_t, 3), "out_t": round(out_t, 3)}
        for p, src_t, out_t in timeline.audio_mixes
    ]

    out_filters_out = [
        {"effect": effect, "out_start": round(s, 3), "out_end": round(e, 3)}
        for effect, s, e in timeline.out_filters
    ]

    data = {
        "session":        session.session_dir.name,
        "total_duration": round(timeline.total_duration, 3),
        "segments":       segments,
        "actors":         actors_out,
        "filters":        filters_out,
        "out_filters":    out_filters_out,
        "overlays":       overlays_out,
        "audio_mixes":    audio_out,
    }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Сохранено: {path}")


# ── Утилита: длина видео через ffprobe ───────────────────────────────────────

def _video_duration(video_path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ], text=True)
    return float(out.strip())


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    from .parser import parse_session
    from .rules import apply_rules
    from .timeline import build_timeline

    p = argparse.ArgumentParser(description="Визуализация тайминга сессии")
    p.add_argument("session_dir", type=Path)
    p.add_argument("--beep-t",   type=float, default=None,
                   help="Позиция бипа в видео (секунды); авто-детекция если не указана")
    p.add_argument("--duration", type=float, default=None,
                   help="Длина видео (авто из video.mp4 если не указана)")
    p.add_argument("--out",      type=Path,  default=None,
                   help="Путь к выходному HTML (по умолчанию session_dir/timeline.html)")
    args = p.parse_args()

    session_dir: Path = args.session_dir.resolve()
    video = session_dir / "video.mp4"

    duration = args.duration
    if duration is None:
        if not video.exists():
            p.error("video.mp4 не найден — укажи --duration вручную")
        duration = _video_duration(video)
        print(f"Длина видео: {duration:.2f}s")

    beep_t = args.beep_t
    if beep_t is None:
        if not video.exists():
            p.error("video.mp4 не найден — укажи --beep-t вручную")
        from .audio_utils import find_beep
        beep_t = find_beep(video)
        print(f"Бип обнаружен: {beep_t:.3f}s")

    session = parse_session(session_dir, beep_t, duration)
    ops     = apply_rules(session)

    tl_inserts = build_timeline(session, ops, apply_cuts=False)
    tl_final   = build_timeline(session, ops, apply_cuts=True)

    print(f"Акторы: { {a: len(ivs) for a, ivs in session.actors.items()} }")
    print(f"Операций: {len(ops)}  "
          f"(Cut={sum(isinstance(o, Cut) for o in ops)}, "
          f"Insert={sum(isinstance(o, Insert) for o in ops)}, "
          f"MixAudio={sum(isinstance(o, MixAudio) for o in ops)})")
    print(f"Inserts-only: {len(tl_inserts.segments)} сегментов, "
          f"total={tl_inserts.total_duration:.1f}s")
    print(f"Final:        {len(tl_final.segments)} сегментов, "
          f"total={tl_final.total_duration:.1f}s")

    ops_no_cut  = [op for op in ops if not isinstance(op, Cut)]
    fig_src     = build_figure(session, ops_no_cut)
    fig_inserts = build_out_figure(session, tl_inserts, title_suffix=" — inserts only")
    fig_final   = build_out_figure(session, tl_final,   title_suffix=" — final")

    out = args.out or session_dir / "timeline.html"
    save_html([fig_src, fig_inserts, fig_final], out)
    save_json(session, ops, out.with_suffix(".json"))
    save_timeline_json(session, tl_inserts, session_dir / "timeline_inserts.json")
    save_timeline_json(session, tl_final,   session_dir / "timeline_final.json")


if __name__ == "__main__":
    main()
