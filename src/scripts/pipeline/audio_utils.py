"""
Утилиты работы с аудио — детекция бипа синхронизации.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


def find_beep(video_path: Path, search_window: float = 15.0) -> float:
    """Найти позицию бипа синхронизации в видеофайле.

    Ищет первый резкий скачок амплитуды в первых search_window секундах.
    Возвращает позицию в секундах от начала видео.
    Бросает ValueError если бип не найден.
    """
    audio, sr = _extract_audio_mono(video_path)
    t = _find_onset(audio, sr, search_window)
    if t is None:
        raise ValueError(
            f"Бип не найден в первых {search_window}s файла {video_path.name}. "
            "Используй --beep-t для ручного указания."
        )
    return t


def _extract_audio_mono(video_path: Path,
                        sample_rate: int = 8000) -> tuple[np.ndarray, int]:
    """Извлечь аудио из видео как float32 mono через ffmpeg."""
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "f32le", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(result.stdout, dtype=np.float32), sample_rate


def _find_onset(audio: np.ndarray, sample_rate: int,
                search_window: float) -> float | None:
    """Первый момент резкого скачка амплитуды (RMS onset)."""
    n_search = min(len(audio), int(search_window * sample_rate))
    chunk = audio[:n_search]

    # RMS в окнах по 50мс с шагом 25мс
    win  = int(0.05 * sample_rate)
    step = win // 2
    rms  = [
        (i / sample_rate, float(np.sqrt(np.mean(chunk[i:i + win] ** 2))))
        for i in range(0, len(chunk) - win, step)
    ]
    if not rms:
        return None

    # Фоновый уровень: медиана первых 2 секунд
    bg_n = max(1, int(2.0 / (step / sample_rate)))
    bg   = float(np.median([v for _, v in rms[:bg_n]])) + 1e-6

    for t, v in rms:
        if v > max(5 * bg, 0.005):
            return t
    return None
