"""
Титровые слайды: рендер и склейка с основным видео.

Читает slides.json из session_dir (автогенерируется pilot при старте).
Формат слайда:
  {"type": "start"|"finish", "duration": 4, "blocks": [
      {"text": "...", "size": 72, "y": 0.35},   # y — доля высоты, x опционален
  ]}
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

SLIDE_FONT     = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SLIDE_FADE_SEC = 1.0


def _probe(video_path: Path) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate",
        "-of", "csv=p=0",
        str(video_path),
    ], text=True).strip()
    w, h, fps_frac = out.split(",")
    num, den = fps_frac.split("/")
    return {"width": int(w), "height": int(h), "fps": float(num) / float(den)}


def _render_slide_frame(blocks: list[dict], alpha: float,
                        width: int, height: int) -> np.ndarray:
    """Один кадр слайда: белая карточка с чёрным текстом на чёрном фоне."""
    from PIL import Image, ImageDraw, ImageFont

    card = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(card)

    for block in blocks:
        size_px = int(block["size"] * height / 1080)
        try:
            font = ImageFont.truetype(SLIDE_FONT, size_px)
        except OSError:
            font = ImageFont.load_default()

        text = block["text"]
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        y_px = int(block["y"] * height) - th // 2
        x_px = int(block["x"] * width) if "x" in block else (width - tw) // 2
        draw.text((x_px, y_px), text, font=font, fill=(0, 0, 0))

    card_np = np.array(card, dtype=np.float32)
    bg      = np.zeros((height, width, 3), dtype=np.float32)
    result  = bg * (1.0 - alpha) + card_np * alpha
    return np.clip(result, 0, 255).astype(np.uint8)


def _generate_slide_video(slide: dict, fps: float,
                          width: int, height: int, out_path: Path) -> None:
    """Записать один слайд как mp4 с тихой аудиодорожкой (для concat).

    Пишем кадры напрямую в ffmpeg через rawvideo pipe → libx264,
    чтобы избежать артефактов cv2.VideoWriter + mp4v.
    Тихая аудиодорожка обязательна: concat demuxer требует одинаковый
    sample_rate во всех сегментах.
    """
    duration = float(slide.get("duration", 4))
    n_total  = int(duration * fps)
    n_fade   = int(SLIDE_FADE_SEC * fps)

    proc = subprocess.Popen([
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={duration}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_path),
    ], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    for i in range(n_total):
        if i < n_fade:
            alpha = i / n_fade
        elif i >= n_total - n_fade:
            alpha = (n_total - i) / n_fade
        else:
            alpha = 1.0
        frame = _render_slide_frame(slide["blocks"], alpha, width, height)
        proc.stdin.write(frame.tobytes())

    proc.stdin.close()
    proc.wait()


def concat_with_slides(main_video: Path, session_dir: Path) -> Path | None:
    """Читает slides.json, рендерит слайды, склеивает: [start…] + main + [finish…].

    Возвращает путь к финальному файлу (<stem>_titled.mp4) или None если
    slides.json не найден или не содержит слайдов.
    """
    slides_path = session_dir / "slides.json"
    if not slides_path.exists():
        print("slides.json не найден, титры пропущены")
        return None

    try:
        slides = json.loads(slides_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Ошибка: slides.json — невалидный JSON: {e}")
        return None

    start_slides  = [s for s in slides if s.get("type") == "start"]
    finish_slides = [s for s in slides if s.get("type") == "finish"]

    if not start_slides and not finish_slides:
        print("slides.json пуст, титры пропущены")
        return None

    probe   = _probe(main_video)
    W, H    = probe["width"], probe["height"]
    fps     = probe["fps"]

    print(f"Слайды: {len(start_slides)} start, {len(finish_slides)} finish  ({W}×{H} {fps:.2f}fps)")

    ordered: list[Path] = []

    for i, slide in enumerate(start_slides):
        p = session_dir / f"_slide_start_{i:02d}.mp4"
        print(f"  Слайд start {i + 1}/{len(start_slides)}…")
        _generate_slide_video(slide, fps, W, H, p)
        ordered.append(p)

    ordered.append(main_video)

    for i, slide in enumerate(finish_slides):
        p = session_dir / f"_slide_finish_{i:02d}.mp4"
        print(f"  Слайд finish {i + 1}/{len(finish_slides)}…")
        _generate_slide_video(slide, fps, W, H, p)
        ordered.append(p)

    # filter_complex concat вместо concat demuxer:
    # demuxer вставляет авто-ресэмплер при смене формата между файлами
    # и падает если channel_layout в result.mp4 повреждён (amix-артефакт).
    # filter_complex явно нормализует каждый аудиопоток через aformat.
    n = len(ordered)
    inputs_args: list[str] = []
    for p in ordered:
        inputs_args += ["-i", str(p.resolve())]

    fc = "".join(
        f"[{i}:a]aformat=channel_layouts=mono:sample_rates=48000[a{i}];"
        for i in range(n)
    )
    fc += "".join(f"[{i}:v][a{i}]" for i in range(n))
    fc += f"concat=n={n}:v=1:a=1[outv][outa]"

    out_path = main_video.with_name(main_video.stem + "_titled.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        *inputs_args,
        "-filter_complex", fc,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path.resolve()),
    ], check=True)
    for p in ordered:
        if p != main_video and p.exists():
            p.unlink()

    return out_path
