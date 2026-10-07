"""
Постобработка сессии: наложение HUD-телеметрии на видео.

Использование:
    python -m scripts.postprocess <session_dir> [--lag СЕКУНДЫ] [--no-cut]
    python scripts/postprocess <session_dir> [--lag СЕКУНДЫ] [--no-cut]

Что делает:
  1. Находит бип в аудиодорожке → вычисляет синхросдвиг лог↔видео
  2. FSM строит сегменты: active / api_pause / zoom_pause
  3. Накладывает HUD: дальномер, напряжение, скорости, метка действия, субтитры
  4. Вырезает паузы ожидания Claude (api_request → api_response)
  5. Сохраняет result.mp4 рядом с session_dir
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pilot.camera import TARGET_WIDTH

from .audio import (
    extract_audio_mono,
    find_beep_position,
    _build_audio_segments,
    _cut_audio,
    _collect_speech_files,
    _mux_final,
)
from .fsm import build_segments, find_segment
from .render import apply_hud
from .slides import _concat_with_slides
from .timeline import load_telemetry, load_events, build_timeline, find_telemetry
from .zoom import (
    ZOOM_ANIM_TARGET_SEC,
    ZOOM_ANIM_PUSH_SEC,
    ZOOM_ANIM_HOLD_SEC,
    ZOOM_ANIM_FADE_SEC,
    generate_zoom_frames,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session_dir")
    parser.add_argument("--lag", type=float, default=0.0,
                        help="Компенсация лага телеметрии в секундах (+ сдвинуть вперёд)")
    parser.add_argument("--no-cut", action="store_true",
                        help="Не вырезать паузы ожидания Claude")
    parser.add_argument("--beep-t", type=float, default=None,
                        help="Ручная позиция бипа в видео (секунды от начала)")
    parser.add_argument("--video", default=None,
                        help="Альтернативный видеофайл (по умолчанию session_dir/video.mp4)")
    parser.add_argument("--speech-volume", type=float, default=5.0,
                        help="Множитель громкости голоса Polly (по умолчанию 5.0)")
    parser.add_argument("--camera-volume", type=float, default=0.3,
                        help="Множитель громкости аудио с камеры (по умолчанию 0.3)")
    parser.add_argument("--skip-render", action="store_true",
                        help="Пропустить рендер кадров, использовать готовый result_noaudio.mp4")
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    video_path  = Path(args.video) if args.video else session_dir / "video.mp4"
    out_path    = video_path.with_name(video_path.stem + "_result.mp4")

    if not video_path.exists():
        print(f"Ошибка: {video_path} не найден")
        sys.exit(1)

    print("Загрузка данных...")
    telem  = load_telemetry(session_dir)
    events = load_events(session_dir)

    sync_event = next((e for e in events if e["type"] == "sync_beep"), None)
    if sync_event is None:
        print("Ошибка: sync_beep не найден в session.jsonl")
        sys.exit(1)
    beep_ts_wall = sync_event["ts_wall"]
    print(f"sync_beep ts_wall = {beep_ts_wall:.3f}")

    session_start_ts = events[0]["ts"] if events else beep_ts_wall

    # --- Найти бип в видео ---
    if args.beep_t is not None:
        video_beep_t = args.beep_t
        print(f"Бип в видео (ручной): {video_beep_t:.3f}s")
    else:
        print("Извлечение аудио для поиска бипа...")
        audio, sr = extract_audio_mono(video_path)
        video_beep_t = find_beep_position(audio, sr)
        if video_beep_t is None:
            print("Автодетект бипа не сработал. Используй --beep-t SEC")
            sys.exit(1)
        print(f"Бип найден в видео: {video_beep_t:.3f}s")

    timeline = build_timeline(events, beep_ts_wall, video_beep_t)

    # --- Видео параметры ---
    cap = cv2.VideoCapture(str(video_path))
    fps      = cap.get(cv2.CAP_PROP_FPS)
    width    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = n_frames / fps
    scale    = width / TARGET_WIDTH
    print(f"Видео: {width}x{height}, {fps:.2f}fps, {n_frames} кадров, {duration:.1f}s")

    # --- Сегменты (FSM) ---
    segments   = build_segments(timeline, duration, no_cut=args.no_cut)
    pause_segs = [s for s in segments if s.kind != "active"]
    zoom_segs  = [s for s in segments if s.kind == "zoom_pause"]
    pauses     = [(s.vt_start, s.vt_end) for s in pause_segs]

    if pauses:
        total_cut = sum(e - s for s, e in pauses)
        print(f"Вырезаем {len(pauses)} пауз, итого {total_cut:.1f}s")
        for s, e in pauses:
            print(f"  {s:.1f}s – {e:.1f}s ({e-s:.1f}s)")
    else:
        print("Паузы не вырезаются")

    # --- Zoom-изображения ---
    W_ai = TARGET_WIDTH
    H_ai = int(height * TARGET_WIDTH / width)
    zoom_images: dict[int, any] = {}
    for zs in zoom_segs:
        if zs.zoom_num not in zoom_images:
            zp = session_dir / f"zoom_{zs.zoom_num:03d}.jpg"
            if zp.exists():
                zoom_images[zs.zoom_num] = cv2.imread(str(zp))
    if zoom_segs:
        print(f"Zoom-анимаций: {len(zoom_segs)} "
              f"(изображений загружено: {len(zoom_images)})")

    # Совместимая карта zoom-пауз для audio._collect_speech_files
    zoom_pause_map = {
        i: {"zoom_num": ps.zoom_num, "cx_ai": ps.zoom_cx, "cy_ai": ps.zoom_cy}
        for i, ps in enumerate(pause_segs)
        if ps.kind == "zoom_pause"
    }

    # --- Обработка кадров ---
    tmp_video = session_dir / "result_noaudio.mp4"

    if args.skip_render and tmp_video.exists():
        print(f"Пропуск рендера кадров (--skip-render), используем {tmp_video}")
        cap.release()
    else:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(tmp_video), fourcc, fps, (width, height))

        def render_frame(frame, t, action, subtitle, turn_n, scale, tokens, operator_overlay):
            apply_hud(frame, t, action, subtitle, turn_n, scale, tokens, operator_overlay)
            return frame

        n_workers = os.cpu_count() // 2 or 4
        MAX_PENDING = n_workers * 8

        print(f"Обработка кадров (потоков: {n_workers})...")
        written = 0
        pending: dict = {}
        next_write = 0

        def flush_ready():
            nonlocal next_write, written
            while next_write in pending:
                fut = pending.pop(next_write)
                writer.write(fut.result())
                written += 1
                next_write += 1
                if written % 100 == 0:
                    print(f"  {written}/{n_frames} кадров...")

        def flush_all():
            flush_ready()
            while pending:
                next(as_completed(pending.values()))
                flush_ready()

        frame_idx = 0
        out_idx = 0
        last_raw_frame = None
        zoom_anim_written: set[int] = set()

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                vt = frame_idx / fps
                frame_idx += 1

                seg = find_segment(segments, vt)
                if seg is None or seg.kind != "active":
                    if (seg is not None
                            and seg.kind == "zoom_pause"
                            and seg.zoom_num not in zoom_anim_written
                            and last_raw_frame is not None):
                        zoom_img = zoom_images.get(seg.zoom_num)
                        if zoom_img is not None:
                            flush_all()
                            anim = generate_zoom_frames(
                                last_raw_frame,
                                seg.zoom_cx, seg.zoom_cy,
                                zoom_img, fps, W_ai, H_ai,
                            )
                            for af in anim:
                                writer.write(af)
                                written += 1
                            zoom_anim_written.add(seg.zoom_num)
                            print(f"  Zoom-анимация zoom_{seg.zoom_num:03d}.jpg: "
                                  f"{len(anim)} кадров")
                    continue

                ts_wall_target = beep_ts_wall + (vt - video_beep_t) + args.lag
                t = find_telemetry(telem, ts_wall_target)

                last_raw_frame = frame.copy()
                pending[out_idx] = pool.submit(
                    render_frame, frame, t, seg.hud_action, seg.subtitle,
                    seg.turn_n, scale, seg.tokens, seg.operator_overlay
                )
                out_idx += 1

                if len(pending) >= MAX_PENDING:
                    flush_ready()

            flush_all()

        cap.release()
        writer.release()
        print(f"Видео без звука: {tmp_video} ({written} кадров)")

    # --- Речь Polly и zoom-тайминги ---
    zoom_anim_sec = (
        max(1, int(ZOOM_ANIM_TARGET_SEC * fps)) +
        max(1, int(ZOOM_ANIM_PUSH_SEC   * fps)) +
        max(1, int(ZOOM_ANIM_HOLD_SEC   * fps)) +
        max(1, int(ZOOM_ANIM_FADE_SEC   * fps))
    ) / fps

    # Позиции тишины в cut-аудио: по одной на каждый zoom-паузу
    zoom_silence_positions: list[float] = []
    for zs in sorted(zoom_segs, key=lambda s: s.vt_start):
        ps = zs.vt_start
        cuts_before = sum(s.vt_end - s.vt_start for s in pause_segs if s.vt_end <= ps)
        zoom_silence_positions.append(ps - cuts_before)
    zoom_silence_positions.sort()

    # --- Аудио: вырезаем паузы + вставляем тишину для zoom-анимаций ---
    if pauses and not args.no_cut:
        print("Нарезка аудио (вырезаем паузы)...")
        audio_segments = _build_audio_segments(duration, pauses)
        camera_audio = _cut_audio(
            video_path, audio_segments, session_dir,
            zoom_silence_positions if zoom_silence_positions else None,
            zoom_anim_sec,
        )
    else:
        camera_audio = None
    speech_files = _collect_speech_files(
        session_dir, timeline, pauses, zoom_pause_map, zoom_anim_sec,
        beep_ts_wall=beep_ts_wall,
        video_beep_t=video_beep_t,
        session_start_ts=session_start_ts,
    )
    print(f"Речевых файлов для наложения: {len(speech_files)}")

    # --- Мукс видео + аудио (камера + Polly) ---
    print(f"Склейка видео+аудио → {tmp_video}")
    _mux_final(tmp_video, video_path, camera_audio, speech_files, out_path,
               speech_volume=args.speech_volume,
               camera_volume=args.camera_volume)
    # result_noaudio.mp4 не удаляем — нужен для повторных запусков с --skip-render
    if camera_audio and camera_audio.exists():
        camera_audio.unlink()

    # --- Титры: start/finish слайды ---
    slides_path = session_dir / "slides.json"
    if slides_path.exists():
        try:
            slides = json.loads(slides_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"Ошибка: slides.json — невалидный JSON: {e}")
            sys.exit(1)
        start_slides  = [s for s in slides if s.get("type") == "start"]
        finish_slides = [s for s in slides if s.get("type") == "finish"]

        if start_slides or finish_slides:
            print(f"Слайды: {len(start_slides)} start, {len(finish_slides)} finish")
            out_path = _concat_with_slides(
                out_path, start_slides, finish_slides, fps, width, height, session_dir
            )
    else:
        print("slides.json не найден, титры пропущены")

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"Готово: {out_path} ({size_mb:.1f} МБ)")


if __name__ == "__main__":
    main()
