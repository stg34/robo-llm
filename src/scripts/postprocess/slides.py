"""
Титровые слайды: рендер, запись видеофайла, склейка с основным видео.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np

# ─── Константы ───────────────────────────────────────────────────────────────

SLIDE_FONT     = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SLIDE_FADE_SEC = 1.0   # длительность fade-in и fade-out


def _render_slide_frame(blocks: list[dict], alpha: float,
                        width: int, height: int) -> np.ndarray:
    """Один кадр слайда: белая карточка с чёрным текстом плавно появляется на чёрном фоне."""
    from PIL import Image, ImageDraw, ImageFont

    card = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(card)

    for block in blocks:
        size_px = int(block["size"] * height / 1080)   # масштаб относительно 1080p
        try:
            font = ImageFont.truetype(SLIDE_FONT, size_px)
        except OSError:
            font = ImageFont.load_default()

        text = block["text"]
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        y_px = int(block["y"] * height) - th // 2   # y — центр строки по вертикали
        if "x" in block:
            x_px = int(block["x"] * width)
        else:
            x_px = (width - tw) // 2

        draw.text((x_px, y_px), text, font=font, fill=(0, 0, 0))

    # Смешиваем белую карточку с чёрным фоном через alpha
    card_np = np.array(card, dtype=np.float32)
    bg = np.zeros((height, width, 3), dtype=np.float32)
    result = bg * (1.0 - alpha) + card_np * alpha
    return np.clip(result, 0, 255).astype(np.uint8)


def generate_slide_video(slide: dict, fps: float,
                         width: int, height: int, out_path: Path):
    """Записать один слайд как видеофайл (с тихой аудиодорожкой для concat)."""
    duration = float(slide.get("duration", 4))
    fade_sec = SLIDE_FADE_SEC
    blocks   = slide["blocks"]

    n_total = int(duration * fps)
    n_fade  = int(fade_sec  * fps)

    tmp_path = out_path.with_suffix(".tmp.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp_path), fourcc, fps, (width, height))

    for i in range(n_total):
        if i < n_fade:
            alpha = i / n_fade
        elif i >= n_total - n_fade:
            alpha = (n_total - i) / n_fade
        else:
            alpha = 1.0
        frame = _render_slide_frame(blocks, alpha, width, height)
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        writer.write(frame_bgr)

    writer.release()

    # Добавляем тихую аудиодорожку — без неё ffmpeg concat теряет аудио основного видео.
    # r=48000 обязателен: concat demuxer требует одинаковый sample_rate во всех сегментах;
    # если слайды 8kHz + основное 48kHz, аудио растягивается в 6 раз.
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(tmp_path),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        str(out_path),
    ], check=True, capture_output=True)
    tmp_path.unlink()


def _concat_with_slides(main_video: Path,
                        start_slides: list[dict], finish_slides: list[dict],
                        fps: float, width: int, height: int,
                        session_dir: Path) -> Path:
    """Сгенерировать слайды и склеить: [start...] + main + [finish...]."""
    tmp_files = []

    for i, slide in enumerate(start_slides):
        p = session_dir / f"_slide_start_{i:02d}.mp4"
        print(f"  Слайд start {i+1}/{len(start_slides)}...")
        generate_slide_video(slide, fps, width, height, p)
        tmp_files.append(("start", p))

    tmp_files.append(("main", main_video))

    for i, slide in enumerate(finish_slides):
        p = session_dir / f"_slide_finish_{i:02d}.mp4"
        print(f"  Слайд finish {i+1}/{len(finish_slides)}...")
        generate_slide_video(slide, fps, width, height, p)
        tmp_files.append(("finish", p))

    # ffmpeg concat — абсолютные пути: ffmpeg резолвит пути в concat-листе
    # относительно расположения самого файла, а не CWD
    concat_list = session_dir / "_concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve()}'" for _, p in tmp_files),
        encoding="utf-8",
    )

    out_path = main_video.with_name(main_video.stem + "_titled.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list.resolve()),
        "-c:v", "libx264", "-c:a", "aac",
        "-movflags", "+faststart",
        str(out_path.resolve()),
    ], check=True, capture_output=False)

    concat_list.unlink()
    for kind, p in tmp_files:
        if kind != "main" and p.exists():
            p.unlink()

    return out_path
