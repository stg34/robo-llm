"""
Превью титровых слайдов в PNG — для подбора шрифтов, размеров и позиций.

Рендерит каждый слайд из slides.json как отдельную картинку (alpha=1.0),
без видео и фейдов. Быстрая итерация: правишь slides.json → смотришь PNG.

    python -m scripts.pipeline.slides_preview <slides.json> [--out DIR]
        [--size 1920x1080] [--like VIDEO.mp4] [--type start|finish]

Размер кадра влияет на текст так же, как в видео: size слайда задан для 1080p
и масштабируется по высоте (size_px = size * height / 1080). Чтобы превью
совпало с финальным видео по геометрии, укажи --like result.mp4.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from .slides import _probe, _render_slide_frame


def render_slides_png(slides_path: Path, out_dir: Path,
                      width: int, height: int,
                      only_type: str | None = None) -> list[Path]:
    slides = json.loads(Path(slides_path).read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for i, slide in enumerate(slides):
        stype = slide.get("type", "slide")
        if only_type and stype != only_type:
            continue
        frame = _render_slide_frame(slide["blocks"], 1.0, width, height)
        out = out_dir / f"slide_{i:02d}_{stype}.png"
        Image.fromarray(frame).save(out)
        print(f"  {out}")
        written.append(out)
    return written


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Рендер слайдов из slides.json в PNG для подбора шрифтов/позиций")
    ap.add_argument("slides", type=Path, help="путь к slides.json")
    ap.add_argument("--out", type=Path, default=None,
                    help="каталог для PNG (по умолчанию <dir slides.json>/slides_preview)")
    ap.add_argument("--size", default="1920x1080",
                    help="разрешение WxH (по умолчанию 1920x1080)")
    ap.add_argument("--like", type=Path, default=None,
                    help="взять W×H из этого видеофайла (переопределяет --size)")
    ap.add_argument("--type", choices=["start", "finish"], default=None,
                    help="рендерить только слайды этого типа")
    args = ap.parse_args()

    if args.like:
        probe = _probe(args.like)
        w, h = probe["width"], probe["height"]
    else:
        w, h = (int(x) for x in args.size.lower().split("x"))

    out_dir = args.out or args.slides.parent / "slides_preview"
    written = render_slides_png(args.slides, out_dir, w, h, args.type)
    print(f"Готово: {len(written)} PNG ({w}×{h}) → {out_dir}")


if __name__ == "__main__":
    main()
