"""
Визуализация FSM-состояний timeline постобработки.

Использование:
    python -m scripts.postprocess.visualize_timeline <session_dir> [--beep-t SEC]
"""
import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from .timeline import load_events, build_timeline
from .fsm import _inject_operator_waits


def _fmt(sec: float) -> str:
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}:{s:05.2f}"


STATE_COLORS = {
    "IDLE":        "#ddeeff",
    "THINKING":    "#ff9944",
    "MOVING":      "#2ecc71",
    "TURNING":     "#3498db",
    "SILENT":      "#f0f0f0",
    "SPEAKING":    "#9b59b6",
    "NONE":        "#f0f0f0",
    "ASKING":      "#f1c40f",
    "ANSWERED":    "#e67e22",
    "WAITING_MSG": "#1abc9c",
}

STATE_TEXT = {
    "IDLE":        "#6688aa",
    "THINKING":    "#7a3a00",
    "MOVING":      "#145a32",
    "TURNING":     "#154360",
    "SILENT":      "#bbb",
    "SPEAKING":    "#5d2a7d",
    "NONE":        "#bbb",
    "ASKING":      "#7d6500",
    "ANSWERED":    "#7a3e00",
    "WAITING_MSG": "#0e6655",
}

COLUMNS = [
    ("API",        "api_state"),
    ("ДВИЖЕНИЕ",   "movement_state"),
    ("РЕЧЬ",       "speech_state"),
    ("ОПЕРАТОР",   "operator_state"),
]

TOOL_COLORS = {
    "move": "#27ae60", "turn_relative": "#2980b9", "turn_absolute": "#2980b9",
    "zoom": "#8e44ad", "done": "#e74c3c", "ask_human": "#f39c12",
    "stop": "#888",    "sleep": "#aaa",   "shoot": "#c0392b",
}


def _replay_fsm(timeline: list[dict], duration: float) -> dict[str, list]:
    events = _inject_operator_waits(timeline)

    api_ch = [(0.0, "IDLE")]
    mov_ch = [(0.0, "IDLE")]
    spe_ch = [(0.0, "SILENT")]
    ope_ch = [(0.0, "NONE")]

    for e in events:
        vt = e["vt"]
        t  = e["type"]

        if t == "api_request_start":
            api_ch.append((vt, "THINKING"))
        elif t == "api_response_end":
            api_ch.append((vt, "IDLE"))

        elif t == "tool_start":
            tool = e.get("tool", "")
            if tool == "move":
                mov_ch.append((vt, "MOVING"))
            elif tool in ("turn_relative", "turn_absolute"):
                mov_ch.append((vt, "TURNING"))
            elif tool == "ask_human":
                ope_ch.append((vt, "ASKING"))

        elif t == "tool_end":
            tool = e.get("tool", "")
            if tool in ("move", "turn_relative", "turn_absolute"):
                mov_ch.append((vt, "IDLE"))
            elif tool == "ask_human":
                ope_ch.append((vt, "NONE"))

        elif t == "speech_start":
            spe_ch.append((vt, "SPEAKING"))
        elif t == "speech_end":
            spe_ch.append((vt, "SILENT"))
            if ope_ch[-1][1] == "ASKING":
                ope_ch.append((vt, "ANSWERED"))

        elif t == "operator_wait_start":
            ope_ch.append((vt, "WAITING_MSG"))
        elif t == "operator_message":
            if ope_ch[-1][1] == "WAITING_MSG":
                ope_ch.append((vt, "NONE"))

    return {
        "api_state":      api_ch,
        "movement_state": mov_ch,
        "speech_state":   spe_ch,
        "operator_state": ope_ch,
    }


def _to_blocks(changes: list[tuple], duration: float) -> list[tuple]:
    blocks = []
    for i, (vt, state) in enumerate(changes):
        end = changes[i + 1][0] if i + 1 < len(changes) else duration
        if end > vt:
            blocks.append((vt, end, state))
    return blocks


def build_html(session_dir: Path, video_beep_t: float) -> str:
    events = load_events(session_dir)
    sync   = next(e for e in events if e["type"] == "sync_beep")
    beep_ts_wall = sync["ts_wall"]

    timeline = build_timeline(events, beep_ts_wall, video_beep_t)
    duration = max(e.get("vt", 0) for e in timeline) + 3.0

    fsm = _replay_fsm(timeline, duration)

    PX  = 5.0       # пикселей на секунду
    AW  = 50        # ширина оси времени
    CW  = 90        # ширина колонки
    GAP = 4         # зазор между колонками
    PT  = 32        # padding top (под заголовки)
    PL  = 8
    LEG = 22        # высота легенды

    track_h = int(duration * PX)
    svg_h   = PT + track_h + LEG + 20
    svg_w   = PL + AW + len(COLUMNS) * (CW + GAP) + 10

    parts: list[str] = []

    def y(t: float) -> float:
        return PT + t * PX

    def cx(col: int) -> int:
        return PL + AW + col * (CW + GAP)

    # Фон колонок
    for i in range(len(COLUMNS)):
        x = cx(i)
        parts.append(f'<rect x="{x}" y="{PT}" width="{CW}" height="{track_h}" '
                     f'fill="#f5f5f5" rx="2"/>')

    # Заголовки
    for i, (title, _) in enumerate(COLUMNS):
        mx = cx(i) + CW // 2
        parts.append(f'<text x="{mx}" y="{PT - 6}" text-anchor="middle" '
                     f'font-size="10" font-weight="bold" fill="#333">{title}</text>')

    # Сетка времени
    t = 0.0
    x0 = PL + AW
    x1 = cx(len(COLUMNS) - 1) + CW
    while t <= duration + 0.1:
        yp = y(t)
        parts.append(f'<line x1="{x0}" y1="{yp:.1f}" x2="{x1}" y2="{yp:.1f}" '
                     f'stroke="#ccc" stroke-width="0.5" stroke-dasharray="2,3"/>')
        parts.append(f'<text x="{x0 - 4}" y="{yp + 3:.1f}" text-anchor="end" '
                     f'font-size="8" fill="#888">{_fmt(t)}</text>')
        t += 10

    # Блоки состояний
    for col_i, (_, fsm_key) in enumerate(COLUMNS):
        x      = cx(col_i)
        blocks = _to_blocks(fsm[fsm_key], duration)
        for vt_s, vt_e, state in blocks:
            yp     = y(vt_s)
            h      = max((vt_e - vt_s) * PX, 1.0)
            color  = STATE_COLORS.get(state, "#ddd")
            tcolor = STATE_TEXT.get(state, "#555")
            tip    = f"{state}\n{_fmt(vt_s)} – {_fmt(vt_e)} ({vt_e - vt_s:.1f}s)"
            parts.append(
                f'<rect x="{x + 1}" y="{yp:.1f}" width="{CW - 2}" height="{h:.1f}" '
                f'fill="{color}" rx="1"><title>{tip}</title></rect>'
            )
            if h >= 10:
                parts.append(
                    f'<text x="{x + CW // 2}" y="{yp + h / 2 + 3:.1f}" '
                    f'text-anchor="middle" font-size="7" fill="{tcolor}">{state}</text>'
                )

    # Кружки tool_start на правом краю первой колонки
    rx = cx(0) + CW + 3
    for e in timeline:
        if e["type"] != "tool_start":
            continue
        tool  = e.get("tool", "?")
        color = TOOL_COLORS.get(tool, "#555")
        yp    = y(e["vt"])
        parts.append(
            f'<circle cx="{rx}" cy="{yp:.1f}" r="3" fill="{color}">'
            f'<title>{tool} @ {_fmt(e["vt"])}</title></circle>'
        )

    # Легенда
    ly  = PT + track_h + 10
    lx  = PL
    active_states = [s for s in STATE_COLORS if s not in ("IDLE", "SILENT", "NONE")]
    for state in active_states:
        c  = STATE_COLORS[state]
        tc = STATE_TEXT[state]
        parts.append(f'<rect x="{lx}" y="{ly}" width="10" height="10" fill="{c}" rx="1"/>')
        parts.append(f'<text x="{lx + 13}" y="{ly + 9}" font-size="9" fill="{tc}">{state}</text>')
        lx += len(state) * 6 + 22

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" '
        f'style="font-family:monospace;background:#fafafa;">\n'
        + "\n".join(parts)
        + "\n</svg>"
    )

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<title>FSM: {session_dir.name}</title>
<style>body{{font-family:monospace;padding:16px;}} h2{{color:#333;}}</style>
</head><body>
<h2>FSM Timeline: {session_dir.name}</h2>
{svg}
</body></html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session_dir")
    parser.add_argument("--beep-t", type=float, default=1.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    html = build_html(session_dir, args.beep_t)

    out = Path(args.out) if args.out else session_dir / "timeline.html"
    out.write_text(html, encoding="utf-8")
    print(f"Сохранено: {out}")
    webbrowser.open(str(out.resolve()))


if __name__ == "__main__":
    main()
