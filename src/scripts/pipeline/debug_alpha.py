"""
python -m scripts.pipeline.debug_alpha <session_dir> [options]

Дамп кривой HUD alpha вокруг границ SynthSegment-ов.
Помогает диагностировать вспышки и неправильные переходы HUD.

Опции:
  --beep-t T     позиция бипа (авто-детекция если не указана)
  --duration D   длина видео (авто если не указана)
  --fps N        кадров в секунду (по умолчанию 30)
  --window W     секунд вокруг каждой границы (по умолчанию 2.0)
  --no-cut       не применять Cut-ы
"""
import argparse
import subprocess
from pathlib import Path

from .parser import parse_session
from .rules import apply_rules, Filter
from .timeline import build_timeline, VideoSegment, SynthSegment
from .hud_pipe import hud_alpha


def _video_duration(video_path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ], text=True)
    return float(out.strip())


def _video_fps(video_path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ], text=True).strip()
    num, den = out.split("/")
    return float(num) / float(den)


def _print_segments(tl) -> None:
    print("=== Сегменты ===")
    for i, seg in enumerate(tl.segments):
        if isinstance(seg, VideoSegment):
            print(f"  [{i:2d}] Video   out={seg.out_start:.3f}–{seg.out_end:.3f}  "
                  f"src={seg.src_start:.3f}–{seg.src_end:.3f}  dur={seg.duration:.3f}s")
        else:
            print(f"  [{i:2d}] Synth   {seg.kind}  "
                  f"out={seg.out_start:.3f}–{seg.out_end:.3f}  dur={seg.duration:.3f}s")


def _print_filters(tl) -> None:
    print("\n=== out_filters (out_t, активны в _hud_alpha) ===")
    if not tl.out_filters:
        print("  (пусто)")
    for effect, out_start, out_end in tl.out_filters:
        print(f"  {effect:20s}  out={out_start:.3f}–{out_end:.3f}  dur={out_end - out_start:.3f}s")

    print("\n=== timeline.filters (src_t, НЕ активны в hud_alpha — игнорируются!) ===")
    if not tl.filters:
        print("  (пусто)")
    for f in tl.filters:
        print(f"  {f.effect:20s}  src={f.src_start:.3f}–{f.src_end:.3f}  dur={f.src_end - f.src_start:.3f}s")


def _src_filter_alpha(filters: list[Filter], src_t: float) -> float:
    """Alpha по timeline.filters в src_t пространстве (то что сейчас игнорируется)."""
    for f in filters:
        if f.src_start <= src_t <= f.src_end:
            dur = max(f.src_end - f.src_start, 1e-6)
            if f.effect == "fade_hud_out":
                return max(0.0, 1.0 - (src_t - f.src_start) / dur)
            if f.effect == "fade_hud_in":
                return (src_t - f.src_start) / dur
    return 1.0


def _alpha_trace(tl, synth: SynthSegment, fps: float, window: float,
                 verbose: bool = False) -> None:
    t_from = max(0.0, synth.out_start - window)
    t_to   = min(tl.total_duration, synth.out_end + window)
    step   = 1.0 / fps

    print(f"\n=== Alpha trace: {synth.kind}  "
          f"[out={synth.out_start:.3f}–{synth.out_end:.3f}] ===")
    print(f"  {'out_t':>8}  {'src_t':>8}  {'segment':18}  "
          f"{'alpha':>6}  {'(src_f)':>8}  note")
    print(f"  {'─'*8}  {'─'*8}  {'─'*18}  {'─'*6}  {'─'*8}  {'─'*20}")

    # Сначала собираем все точки
    points: list[tuple[float, str | None, float, float | None, str]] = []
    out_t = t_from
    while out_t <= t_to + step * 0.5:
        seg_label = ""
        src_t = None
        for seg in tl.segments:
            if seg.out_start <= out_t <= seg.out_end:
                if isinstance(seg, VideoSegment):
                    seg_label = "VideoSegment"
                    src_t = seg.src_start + (out_t - seg.out_start)
                else:
                    seg_label = f"Synth:{seg.kind[:12]}"
                break

        alpha     = hud_alpha(tl.filters, tl.out_filters,
                              src_t if src_t is not None else 0.0, out_t)
        src_alpha = _src_filter_alpha(tl.filters, src_t) if src_t is not None else None

        note = ""
        if abs(out_t - synth.out_start) < step * 0.5:
            note = "◄ SYNTH START"
        elif abs(out_t - synth.out_end) < step * 0.5:
            note = "◄ SYNTH END"

        points.append((out_t, src_t, alpha, src_alpha, seg_label, note))
        out_t = round((out_t + step) * fps) / fps

    # Определяем «интересные» точки
    def _is_interesting(i: int) -> bool:
        if verbose:
            return True
        out_t, src_t, alpha, src_alpha, seg_label, note = points[i]
        if note:                                          # граница SynthSegment
            return True
        if src_alpha is not None and src_alpha < 0.999:  # src_filter активен
            return True
        if alpha < 0.999:                                # alpha не полная
            return True
        # сосед меняет alpha
        for j in (i - 1, i + 1):
            if 0 <= j < len(points) and points[j][2] < 0.999:
                return True
        return False

    # Печать с свёрткой плоских зон
    skip_start = None
    for i, (out_t, src_t, alpha, src_alpha, seg_label, note) in enumerate(points):
        if _is_interesting(i):
            if skip_start is not None:
                skipped = i - skip_start
                print(f"  {'...':>8}  {'':>8}  {'':18}  {'':>6}  {'':>8}  "
                      f"({skipped} кадров alpha=1.000)")
                skip_start = None
            prev_alpha = points[i - 1][2] if i > 0 else None
            if note == "" and prev_alpha is not None and abs(alpha - prev_alpha) > 0.005:
                note = f"Δ={alpha - prev_alpha:+.3f}"
            src_t_str = f"{src_t:.3f}" if src_t is not None else "  ---  "
            src_a_str = f"{src_alpha:.3f}" if src_alpha is not None else "  ---  "
            print(f"  {out_t:8.3f}  {src_t_str:>8}  {seg_label:18}  "
                  f"{alpha:6.3f}  {src_a_str:>8}  {note}")
        else:
            if skip_start is None:
                skip_start = i
    if skip_start is not None:
        skipped = len(points) - skip_start
        print(f"  {'...':>8}  {'':>8}  {'':18}  {'':>6}  {'':>8}  "
              f"({skipped} кадров alpha=1.000)")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Дамп кривой HUD alpha вокруг зум-переходов"
    )
    p.add_argument("session_dir", type=Path)
    p.add_argument("--beep-t",   type=float, default=None)
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--fps",      type=float, default=None,
                   help="FPS видео (авто из video.mp4 если не указан)")
    p.add_argument("--window",   type=float, default=2.0,
                   help="Секунд вокруг каждой границы SynthSegment (по умолчанию 2.0)")
    p.add_argument("--no-cut",   action="store_true")
    p.add_argument("--verbose",  action="store_true",
                   help="Показывать все кадры, не сворачивать плоские зоны")
    args = p.parse_args()

    session_dir = args.session_dir.resolve()
    video = session_dir / "video.mp4"

    duration = args.duration
    if duration is None:
        duration = _video_duration(video)
        print(f"Длина видео: {duration:.2f}s")

    fps = args.fps
    if fps is None:
        fps = _video_fps(video)
        print(f"FPS видео: {fps:.4f}")

    beep_t = args.beep_t
    if beep_t is None:
        from .audio_utils import find_beep
        beep_t = find_beep(video)
        print(f"Бип: {beep_t:.3f}s")

    session  = parse_session(session_dir, beep_t, duration)
    ops      = apply_rules(session)
    timeline = build_timeline(session, ops, apply_cuts=not args.no_cut)

    _print_segments(timeline)
    _print_filters(timeline)

    synths = [s for s in timeline.segments if isinstance(s, SynthSegment)]
    if not synths:
        print("\nНет SynthSegment-ов — нечего трейсить.")
        return

    for synth in synths:
        _alpha_trace(timeline, synth, fps, args.window, verbose=args.verbose)


if __name__ == "__main__":
    main()
