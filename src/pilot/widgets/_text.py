"""
Вспомогательный модуль: рендер текста через PIL/Pillow.

Поддерживает кириллицу и любые другие символы TTF-шрифта.
Работает быстро: конвертируется только маленький буфер под текст,
а не весь кадр.
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --- Путь к шрифту ---
# Ищем DejaVu Sans (есть на большинстве Linux-систем).
# Можно переопределить, положив шрифт рядом с проектом.
_FONT_CANDIDATES = [
    Path(__file__).parent.parent.parent / "assets" / "font.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf"),
]

_FONT_PATH: Path | None = next((p for p in _FONT_CANDIDATES if p.exists()), None)

if _FONT_PATH is None:
    raise FileNotFoundError(
        "Не найден TTF-шрифт с кириллицей. "
        "Установите: sudo apt install fonts-dejavu-core  "
        "или положите шрифт в assets/font.ttf"
    )

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _get_font(size_px: int) -> ImageFont.FreeTypeFont:
    if size_px not in _font_cache:
        _font_cache[size_px] = ImageFont.truetype(str(_FONT_PATH), size_px)
    return _font_cache[size_px]


def get_text_size(text: str, size_px: int) -> tuple[int, int]:
    """Вернуть (ширина, высота) текста в пикселях."""
    font = _get_font(size_px)
    bbox = font.getbbox(text)           # (left, top, right, bottom)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def put_text(frame: np.ndarray, text: str,
             pos: tuple[int, int], size_px: int,
             color_bgr: tuple[int, int, int]) -> None:
    """
    Нарисовать текст на кадре (BGR numpy array).

    Параметры:
        frame     — кадр, модифицируется на месте
        text      — строка (поддерживается кириллица)
        pos       — (x, y) левый нижний угол текста (как в cv2.putText)
        size_px   — размер шрифта в пикселях
        color_bgr — цвет в BGR
    """
    if not text:
        return

    font = _get_font(size_px)
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    if tw <= 0 or th <= 0:
        return

    # Небольшой буфер под текст (чёрный фон → только цветные пиксели)
    buf = Image.new("RGB", (tw + 2, th + 2), (0, 0, 0))
    draw = ImageDraw.Draw(buf)
    r, g, b = color_bgr[2], color_bgr[1], color_bgr[0]  # BGR → RGB
    draw.text((-bbox[0] + 1, -bbox[1] + 1), text, font=font, fill=(r, g, b))

    txt = np.array(buf)[..., ::-1]  # RGB → BGR numpy

    # pos — левый нижний угол (как cv2): сдвигаем y вверх на высоту
    x, y = pos
    y1 = y - th - 1
    y2 = y1 + txt.shape[0]
    x1 = x
    x2 = x + txt.shape[1]

    # Обрезаем по границам кадра
    fh, fw = frame.shape[:2]
    if x2 <= 0 or y2 <= 0 or x1 >= fw or y1 >= fh:
        return
    fx1, fx2 = max(x1, 0), min(x2, fw)
    fy1, fy2 = max(y1, 0), min(y2, fh)
    tx1, tx2 = fx1 - x1, fx1 - x1 + (fx2 - fx1)
    ty1, ty2 = fy1 - y1, fy1 - y1 + (fy2 - fy1)

    roi     = frame[fy1:fy2, fx1:fx2]
    txt_roi = txt[ty1:ty2, tx1:tx2]

    # Аддитивное наложение: рисуем только там, где есть пиксели текста
    mask = txt_roi.any(axis=2)
    roi[mask] = np.clip(
        roi[mask].astype(np.int16) + txt_roi[mask].astype(np.int16),
        0, 255,
    ).astype(np.uint8)
