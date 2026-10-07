"""
python -m scripts.pipeline <session_dir> [--beep-t T] [options]

Опции:
  --beep-t T          позиция бипа в видеофайле (секунды); авто-детекция если не указана
  --duration D        длина видео (авто из video.mp4 если не указана)
  --speech-volume N   громкость TTS относительно камеры (по умолчанию 5.0)
  --camera-volume N   громкость камеры (по умолчанию 0.3)
  --no-cut            не вырезать паузы (только вставки, для проверки)
  --no-hud            не накладывать HUD (быстрее, для отладки монтажа)
  --no-slides         не добавлять титровые слайды
  --final             чистовой HUD с GaussianBlur (по умолчанию — без блура)
  --debug             показывать A/V sync-лог по каждому сегменту
  --out PATH          путь к выходному файлу (по умолчанию session_dir/result.mp4)
"""
import argparse
import subprocess
from pathlib import Path

from .parser import parse_session
from .rules import apply_rules
from .timeline import build_timeline
from .render import render


def _video_duration(video_path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ], text=True)
    return float(out.strip())


def main() -> None:
    p = argparse.ArgumentParser(
        description="Постобработка сессии робота: вставки + вырезание пауз → result.mp4"
    )
    p.add_argument("session_dir",     type=Path)
    p.add_argument("--beep-t",        type=float, default=None,
                   help="Позиция бипа в видеофайле (секунды); авто-детекция если не указана")
    p.add_argument("--duration",      type=float, default=None,
                   help="Длина видео (авто из video.mp4 если не указана)")
    p.add_argument("--speech-volume", type=float, default=5.0)
    p.add_argument("--camera-volume", type=float, default=0.3)
    p.add_argument("--no-cut",        action="store_true",
                   help="Не вырезать паузы (только вставки)")
    p.add_argument("--no-hud",        action="store_true",
                   help="Не накладывать HUD (быстрее, для отладки монтажа)")
    p.add_argument("--no-slides",     action="store_true",
                   help="Не добавлять титровые слайды")
    p.add_argument("--final",         action="store_true",
                   help="Чистовой HUD: с GaussianBlur (медленнее, лучше качество)")
    p.add_argument("--debug",         action="store_true",
                   help="Показывать A/V sync-лог по каждому сегменту")
    p.add_argument("--out",           type=Path, default=None,
                   help="Выходной файл (по умолчанию session_dir/result.mp4)")
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

    session  = parse_session(session_dir, beep_t, duration)
    ops      = apply_rules(session)
    timeline = build_timeline(session, ops, apply_cuts=not args.no_cut)

    from .rules import Cut, Insert, MixAudio
    print(f"Операций: {len(ops)} "
          f"(Cut={sum(isinstance(o, Cut) for o in ops)}, "
          f"Insert={sum(isinstance(o, Insert) for o in ops)}, "
          f"MixAudio={sum(isinstance(o, MixAudio) for o in ops)})")
    print(f"Сегментов: {len(timeline.segments)}, total={timeline.total_duration:.1f}s")

    suffix = "_nocut" if args.no_cut else ""
    out = args.out or session_dir / f"result{suffix}.mp4"
    if not args.final and not args.no_hud:
        from .hud_pipe import set_draft_mode
        set_draft_mode()
        print("Черновой режим (по умолчанию): GaussianBlur отключён. Используй --final для чистового.")

    render(session, timeline, out,
           speech_volume=args.speech_volume,
           camera_volume=args.camera_volume,
           apply_hud=not args.no_hud,
           debug=args.debug)

    if not args.no_slides:
        from .slides import concat_with_slides
        titled = concat_with_slides(out, session_dir)
        if titled:
            print(f"С титрами: {titled}")


if __name__ == "__main__":
    main()
