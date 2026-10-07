"""
Аудио: извлечение, обнаружение бипа, нарезка пауз, наложение речи Polly.
"""
import json
import subprocess
from pathlib import Path

import numpy as np


def extract_audio_mono(video_path: Path, sample_rate: int = 8000) -> tuple[np.ndarray, int]:
    """Вытащить аудио из видео как float32 numpy array через ffmpeg."""
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "f32le", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(result.stdout, dtype=np.float32), sample_rate


def find_beep_position(audio: np.ndarray, sample_rate: int,
                       search_window: float = 15.0) -> float | None:
    """Найти начало бипа: первый резкий скачок амплитуды в search_window секундах.

    Возвращает позицию в секундах от начала видео, или None.
    """
    n_search = min(len(audio), int(search_window * sample_rate))
    chunk = audio[:n_search]

    # RMS в окнах по 50мс с шагом 25мс
    win = int(0.05 * sample_rate)
    step = win // 2
    rms = []
    for i in range(0, len(chunk) - win, step):
        val = float(np.sqrt(np.mean(chunk[i:i + win] ** 2)))
        rms.append((i / sample_rate, val))

    if not rms:
        return None

    # Фоновый уровень: медиана первых 2 секунд
    bg_n = max(1, int(2.0 / (step / sample_rate)))
    bg = float(np.median([v for _, v in rms[:bg_n]])) + 1e-6

    # Первый момент когда RMS > 5× фон и > 0.005
    for t, v in rms:
        if v > max(5 * bg, 0.005):
            return t

    return None


def _build_audio_segments(duration: float, pauses: list[tuple]) -> list[tuple]:
    """Список интервалов (start, end) которые ОСТАВЛЯЕМ в аудио."""
    segments = []
    cursor = 0.0
    for ps, pe in sorted(pauses):
        if ps > cursor:
            segments.append((cursor, ps))
        cursor = pe
    if cursor < duration:
        segments.append((cursor, duration))
    return segments


def _cut_audio(video_path: Path, segments: list[tuple], session_dir: Path,
               zoom_silence_positions: list[float] | None = None,
               zoom_anim_sec: float = 0.0) -> Path:
    """Вырезать паузы из аудио; если переданы zoom_silence_positions — вставить тишину.

    zoom_silence_positions: позиции (в секундах cut-аудио) где вставить тишину
                            длиной zoom_anim_sec — по одной на каждую zoom-анимацию.

    Реализация через PCM: извлекаем сырой float32, вставляем тишину numpy'ем,
    кодируем обратно в AAC — надёжнее filter_complex с asplit/concat.
    """
    TARGET_SR = 48000
    out = session_dir / "_audio_merged.aac"
    select_expr = "+".join(f"between(t,{s},{e})" for s, e in segments)

    # Извлечь cut-аудио как raw PCM (mono 48kHz float32)
    pcm_path = session_dir / "_tmp_cut.f32le"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path), "-vn",
            "-af", (f"aselect='{select_expr}',asetpts=N/SR/TB,"
                    f"aresample={TARGET_SR},aformat=channel_layouts=mono"),
            "-f", "f32le", str(pcm_path),
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(e.stderr.decode(errors="replace"))
        raise

    audio = np.frombuffer(pcm_path.read_bytes(), dtype=np.float32)
    pcm_path.unlink()

    # Вставить тишину numpy'ем в позиции zoom-анимаций
    if zoom_silence_positions:
        silence_samples = int(zoom_anim_sec * TARGET_SR)
        silence = np.zeros(silence_samples, dtype=np.float32)
        parts: list[np.ndarray] = []
        prev = 0
        for pos_sec in sorted(zoom_silence_positions):
            insert_at = int(pos_sec * TARGET_SR)
            parts.append(audio[prev:insert_at])
            parts.append(silence)
            prev = insert_at
        parts.append(audio[prev:])
        audio = np.concatenate(parts)

    # Закодировать в AAC через pipe
    proc = subprocess.Popen([
        "ffmpeg", "-y",
        "-f", "f32le", "-ar", str(TARGET_SR), "-ac", "1", "-i", "pipe:0",
        "-c:a", "aac", str(out),
    ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    _, stderr = proc.communicate(input=audio.tobytes())
    if proc.returncode != 0:
        print(stderr.decode(errors="replace"))
        raise subprocess.CalledProcessError(proc.returncode, "ffmpeg encode aac")
    return out


def _collect_speech_files(session_dir: Path, timeline: list[dict],
                          pauses: list[tuple],
                          zoom_pause_map: dict,
                          zoom_anim_sec: float,
                          beep_ts_wall: float | None = None,
                          video_beep_t: float | None = None,
                          session_start_ts: float | None = None) -> list[tuple[Path, float]]:
    """Собрать список (mp3_path, video_t) для наложения голоса Polly.

    video_t — момент в итоговом видео (с учётом вырезанных пауз и zoom-анимаций).

    Если в session_dir существует speech_map.json, тайминги берутся из него.
    Формат speech_map.json: {"speech_001.mp3": 12.68, "speech_002.mp3": 144.52}
    Значения — секунды от начала сессии (session_start_ts = 0).
    Преобразование: vt = session_relative_t - (beep_ts_wall - session_start_ts) + video_beep_t

    Если speech_map.json нет — используется speech_start логика из timeline.
    """

    def cut_before(vt: float) -> float:
        total = 0.0
        for ps, pe in sorted(pauses):
            if pe <= vt:
                total += pe - ps
            elif ps < vt:
                total += vt - ps
        return total

    def zoom_added_before(vt: float) -> float:
        """Суммарная длительность zoom-анимаций, вставленных до момента vt."""
        total = 0.0
        for pidx in zoom_pause_map:
            ps = pauses[pidx][0]
            if ps < vt:
                total += zoom_anim_sec
        return total

    result = []

    # --- Override из speech_map.json ---
    speech_map_path = session_dir / "speech_map.json"
    if speech_map_path.exists():
        speech_map = json.loads(speech_map_path.read_text(encoding="utf-8"))
        # session_start_ts, beep_ts_wall и video_beep_t обязательны при использовании карты
        if beep_ts_wall is None or video_beep_t is None or session_start_ts is None:
            raise ValueError(
                "speech_map.json найден, но beep_ts_wall / video_beep_t / "
                "session_start_ts не переданы в _collect_speech_files"
            )
        # Сортируем по времени
        entries = sorted(speech_map.items(), key=lambda kv: kv[1])
        beep_offset = beep_ts_wall - session_start_ts  # смещение бипа от начала сессии
        for filename, session_relative_t in entries:
            mp3 = session_dir / filename
            if not mp3.exists():
                continue
            # Перевод в video-time: session_relative_t → vt
            vt = session_relative_t - beep_offset + video_beep_t
            # Если vt попал на границу паузы (округление) — сдвигаем на конец паузы
            for ps, pe in pauses:
                if ps <= vt < pe:
                    vt = pe
                    break
            out_vt = vt - cut_before(vt) + zoom_added_before(vt)
            result.append((mp3, out_vt))
            print(f"  {filename} → {out_vt:.1f}s (из speech_map.json)")
        return result

    # --- Fallback: speech_start события из timeline ---
    speech_events = [(e["vt"], e) for e in timeline if e["type"] == "speech_start"]
    speech_events.sort()

    for i, (vt, e) in enumerate(speech_events):
        mp3 = session_dir / f"speech_{i + 1:03d}.mp3"
        if not mp3.exists():
            continue
        for ps, pe in pauses:
            if ps <= vt < pe:
                vt = pe
                break
        out_vt = vt - cut_before(vt) + zoom_added_before(vt)
        result.append((mp3, out_vt))
        print(f"  speech_{i+1:03d}.mp3 → {out_vt:.1f}s в итоговом видео")
    return result


def _mux_final(hud_video: Path, orig_video: Path, camera_audio: Path | None,
               speech_files: list[tuple[Path, float]], out_path: Path,
               speech_volume: float = 5.0, camera_volume: float = 0.3):
    """Склеить HUD-видео + аудио камеры + голос Polly через ffmpeg filter_complex."""

    # Входные файлы
    inputs = ["-i", str(hud_video)]
    if camera_audio:
        inputs += ["-i", str(camera_audio)]
    else:
        inputs += ["-i", str(orig_video)]  # аудио из оригинала
    for mp3, _ in speech_files:
        inputs += ["-i", str(mp3)]

    # filter_complex: volume + adelay для каждого speech mp3, затем amix всего
    filter_parts = []

    # Аудио камеры — убавляем фоновый шум
    filter_parts.append(f"[1:a]volume={camera_volume}[cam]")
    cam_label = "[cam]"

    # Каждый speech mp3: усиливаем громкость и сдвигаем на нужное время
    speech_labels = []
    for j, (_, vt) in enumerate(speech_files):
        delay_ms = int(vt * 1000)
        in_idx = 2 + j
        label = f"[s{j}]"
        filter_parts.append(
            f"[{in_idx}:a]volume={speech_volume},adelay={delay_ms}|{delay_ms}{label}"
        )
        speech_labels.append(label)

    # amix: камера + все speech
    all_audio = cam_label + "".join(speech_labels)
    n_mix = 1 + len(speech_labels)
    filter_parts.append(
        f"{all_audio}amix=inputs={n_mix}:duration=longest:dropout_transition=0:normalize=0[aout]"
    )

    filter_complex = "; ".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=False)
