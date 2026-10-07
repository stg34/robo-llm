"""
Настройки из переменных окружения (.env).

Загружается один раз при импорте.
"""
import math
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SERIAL_PORT       = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")

LLM_MODEL        = os.getenv("LLM_MODEL", "claude-opus-4-6")
LLM_TEMPERATURE  = float(os.getenv("LLM_TEMPERATURE")) if os.getenv("LLM_TEMPERATURE") else None

# Ключи по провайдерам
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
_GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
_GROK_API_KEY      = os.getenv("GROK_API_KEY", "")

# Base URL по провайдерам (OpenAI-совместимые)
_OPENAI_BASE_URL   = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
_GEMINI_BASE_URL   = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
_GROK_BASE_URL     = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1")

# Устаревшие универсальные переменные (фоллбэк)
_LLM_API_KEY_LEGACY  = os.getenv("LLM_API_KEY") or _ANTHROPIC_API_KEY
_LLM_BASE_URL_LEGACY = os.getenv("LLM_BASE_URL") or None

# Для обратной совместимости (используется в brain.py / __main__.py напрямую)
LLM_API_KEY  = _LLM_API_KEY_LEGACY
LLM_BASE_URL = _LLM_BASE_URL_LEGACY


def resolve_llm_credentials(model: str) -> tuple[str, str | None]:
    """Возвращает (api_key, base_url) для указанной модели по её имени."""
    m = model.lower()
    if m.startswith("claude"):
        return _ANTHROPIC_API_KEY, "https://api.anthropic.com"
    if m.startswith("gemini"):
        return _GEMINI_API_KEY, _GEMINI_BASE_URL
    if m.startswith("grok"):
        return _GROK_API_KEY, _GROK_BASE_URL
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return _OPENAI_API_KEY, _OPENAI_BASE_URL
    # неизвестный провайдер — фоллбэк на LLM_API_KEY / LLM_BASE_URL
    return _LLM_API_KEY_LEGACY, _LLM_BASE_URL_LEGACY

HUD_SHOW_TOKENS  = os.getenv("HUD_SHOW_TOKENS", "1") != "0"
HUD_SHOW_LIGHTS  = os.getenv("HUD_SHOW_LIGHTS", "1") != "0"

ROBOT_NAME = os.getenv("ROBOT_NAME", "FRANCY")

HUD_BLOCK_X = int(os.getenv("HUD_BLOCK_X", "24"))
HUD_BLOCK_Y = int(os.getenv("HUD_BLOCK_Y", "24"))


def robot_hud_title(model: str | None = None) -> str:
    return f"{ROBOT_NAME}  {model or LLM_MODEL}"

TAPO_HOST          = os.getenv("TAPO_HOST", "")
TAPO_USER          = os.getenv("TAPO_USER", "")
TAPO_PASSWORD      = os.getenv("TAPO_PASSWORD", "")
# Облачный логин (admin) нужен для ffmpeg — локальный (camera) ffmpeg не пускает
TAPO_CLOUD_USER    = os.getenv("TAPO_CLOUD_USER", TAPO_USER)
TAPO_CLOUD_PASSWORD = os.getenv("TAPO_CLOUD_PASSWORD", TAPO_PASSWORD)

AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "eu-west-1")

MIN_BATTERY_VOLTAGE = float(os.getenv("MIN_BATTERY_VOLTAGE", "11.0"))

# --- Калибровка дальномера ---
# Горизонтальное смещение лазера от центра кадра: доля ширины (0.0–1.0); None = центр.
_RF_CX = float(os.getenv("RANGEFINDER_CX")) if os.getenv("RANGEFINDER_CX") else None

# Фокусное расстояние камеры (пикс.) при опорной ширине 896px и FOV 75.2°.
RANGEFINDER_FOCAL_PX = float(os.getenv("RANGEFINDER_FOCAL_PX", "582"))

# Угол наклона камеры вниз (градусы). Измерить уровнем на корпусе камеры.
CAMERA_TILT_DEG = float(os.getenv("CAMERA_TILT_DEG", "0"))

# Вертикальное смещение дальномера ниже камеры (мм). Измерить рулеткой.
RANGEFINDER_VERTICAL_OFFSET = float(os.getenv("RANGEFINDER_VERTICAL_OFFSET", "0"))

# Физический радиус виртуальной мишени (мм) — определяет размер круга прицела.
RANGEFINDER_TARGET_RADIUS = float(os.getenv("RANGEFINDER_TARGET_RADIUS", "150"))


_REFERENCE_WIDTH = 896  # ширина кадра, для которой задан RANGEFINDER_FOCAL_PX


def rangefinder_pos(frame_shape, range_mm: int = 0) -> tuple[int | None, int | None]:
    """Возвращает (cx, cy) по pinhole-модели камеры.

    cy вычисляется из угла наклона камеры и вертикального смещения дальномера.
    При range_mm=0 — позиция для d→∞ (только наклон камеры).
    """
    H, W = frame_shape[:2]
    cx = int(_RF_CX * W) if _RF_CX is not None else None

    # Масштабируем фокусное расстояние под реальную ширину кадра
    focal = RANGEFINDER_FOCAL_PX * W / _REFERENCE_WIDTH

    alpha = math.radians(CAMERA_TILT_DEG)
    dh = RANGEFINDER_VERTICAL_OFFSET
    if range_mm > 0:
        z_cam = dh * math.sin(alpha) + range_mm * math.cos(alpha)
        y_cam = -dh * math.cos(alpha) + range_mm * math.sin(alpha)
        cy = H // 2 - int(focal * y_cam / z_cam)
    else:
        cy = H // 2 - int(focal * math.tan(alpha))

    cy = max(-H, min(2 * H, cy))
    return cx, cy


def rangefinder_radius(range_mm: int) -> int:
    """Радиус прицела в опорных пикселях (до умножения на scale).

    Физически: угловой размер мишени радиуса RANGEFINDER_TARGET_RADIUS мм
    на расстоянии range_mm мм, спроецированный в пиксели через focal_px.
    atan не уходит в бесконечность на малых дистанциях.
    """
    if range_mm <= 0:
        return int(RANGEFINDER_FOCAL_PX * math.atan(RANGEFINDER_TARGET_RADIUS / 1000))
    return max(6, int(RANGEFINDER_FOCAL_PX * math.atan(RANGEFINDER_TARGET_RADIUS / range_mm)))

# RTSP URL для OpenCV (локальный пользователь)
def tapo_rtsp_url() -> str:
    return f"rtsp://{TAPO_USER}:{TAPO_PASSWORD}@{TAPO_HOST}:554/stream1"

# RTSP URL для ffmpeg-записи (облачный/admin пользователь)
def tapo_rtsp_url_record() -> str:
    return f"rtsp://{TAPO_CLOUD_USER}:{TAPO_CLOUD_PASSWORD}@{TAPO_HOST}:554/stream1"
