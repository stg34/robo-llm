"""
Layer 3b — ffmpeg-python рендер результирующего видео.

Вход:  ParsedSession + Timeline → result.mp4
Выход: смонтированное видео с HUD и речью

VideoSegment  → trim+HUD → video-only mp4 → ffmpeg concat
SynthSegment  → кадры OpenCV (zoom_animation) → temp mp4 → ffmpeg concat
build_audio() → оригинал + речь в сэмпловом домене → срезы по Timeline → WAV → mux
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import ffmpeg
import numpy as np

from .parser import ParsedSession
from .timeline import Timeline, VideoSegment, SynthSegment

# Разрешение, с которым AI видел кадр (координаты cx/cy)
_AI_WIDTH = 896

# FPS для zoom-анимации
_ZOOM_FPS = 15.0


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _probe(video_path: Path) -> dict:
    """Вернуть ширину, высоту и fps видео через ffprobe."""
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


def _probe_duration(path: Path) -> tuple[float, float]:
    """Вернуть (video_duration, audio_duration) через ffprobe."""
    def stream_dur(select: str) -> float:
        try:
            out = subprocess.check_output([
                "ffprobe", "-v", "error",
                "-select_streams", select,
                "-show_entries", "stream=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ], text=True).strip()
            return float(out) if out else 0.0
        except Exception:
            return 0.0
    return stream_dur("v:0"), stream_dur("a:0")


def _extract_frame(video_path: Path, src_t: float) -> np.ndarray:
    """Извлечь один кадр из видео в момент src_t (BGR numpy array)."""
    out, _ = (
        ffmpeg
        .input(str(video_path), ss=src_t)
        .output("pipe:", vframes=1, format="rawvideo", pix_fmt="bgr24")
        .run(capture_stdout=True, capture_stderr=True)
    )
    probe = _probe(video_path)
    W, H = probe["width"], probe["height"]
    return np.frombuffer(out, dtype=np.uint8).reshape((H, W, 3))


def _write_frames_to_mp4(frames: list[np.ndarray], path: Path,
                          fps: float) -> None:
    """Записать список BGR-кадров в mp4 с тихой аудиодорожкой."""
    if not frames:
        return
    H, W = frames[0].shape[:2]
    duration = len(frames) / fps
    proc = subprocess.Popen([
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={duration}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for f in frames:
        proc.stdin.write(f.tobytes())
    proc.stdin.close()
    proc.wait()


# ── Zoom-анимация ─────────────────────────────────────────────────────────────

def _render_zoom_segment(seg: SynthSegment, session: ParsedSession,
                         video_path: Path, zoom_num: int,
                         W: int, H: int) -> Path:
    """Сгенерировать temp mp4 для zoom_animation SynthSegment."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from scripts.postprocess.zoom import generate_zoom_frames

    # Кадр из источника: чуть раньше начала Cut (≈ конец pre_zoom)
    src_t = max(0.0, seg.src_t - 0.1)
    src_frame = _extract_frame(video_path, src_t)

    # Результат зума из zoom_NNN.jpg
    zoom_jpg = session.session_dir / f"zoom_{zoom_num:03d}.jpg"
    zoom_result = cv2.imread(str(zoom_jpg)) if zoom_jpg.exists() else src_frame

    cx_ai = int(seg.meta.get("cx", W // 2))
    cy_ai = int(seg.meta.get("cy", H // 2))
    W_ai  = _AI_WIDTH
    H_ai  = int(H * _AI_WIDTH / W)

    frames = generate_zoom_frames(
        src_frame, cx_ai, cy_ai, zoom_result, _ZOOM_FPS, W_ai, H_ai
    )

    tmp = session.session_dir / f"_tmp_zoom_{zoom_num:03d}.mp4"
    _write_frames_to_mp4(frames, tmp, _ZOOM_FPS)
    return tmp


# ── Аудио в сэмпловом домене ──────────────────────────────────────────────────

_AUDIO_SR = 48000


def build_audio(
    timeline:       "Timeline",
    video_path:     Path,
    camera_volume:  float,
    speech_volume:  float,
    sample_rate:    int = _AUDIO_SR,
) -> np.ndarray:
    """Собрать аудиодорожку результата в сэмпловом домене.

    1. Декодировать оригинал video.mp4 целиком → float32 numpy (src_t).
    2. Подмешать речь Polly в src_t позиции (до вырезания).
    3. Реконструировать по Timeline:
         VideoSegment  → срез оригинала
         SynthSegment  → тишина (zoom-анимация)
    Одна AAC-кодировка в конце — нет накопленного encoder-delay.
    """
    # 1. Оригинал
    proc = subprocess.run(
        [
            "ffmpeg",
            "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "f32le", "pipe:1",
        ],
        capture_output=True, check=True,
    )
    audio = np.frombuffer(proc.stdout, dtype=np.float32).copy() * camera_volume
    total = len(audio)

    # 2. Речь в src_t
    for mp3_path, src_t, _out_t in timeline.audio_mixes:
        proc2 = subprocess.run(
            [
                "ffmpeg",
                "-i", str(mp3_path),
                "-vn", "-ac", "1", "-ar", str(sample_rate),
                "-f", "f32le", "pipe:1",
            ],
            capture_output=True, check=True,
        )
        speech = np.frombuffer(proc2.stdout, dtype=np.float32) * speech_volume
        s = int(src_t * sample_rate)
        e = min(s + len(speech), total)
        if s < total:
            audio[s:e] += speech[:e - s]

    # 3. Реконструкция по Timeline
    from .timeline import VideoSegment, SynthSegment
    pieces: list[np.ndarray] = []
    for seg in timeline.segments:
        if isinstance(seg, VideoSegment):
            s = int(seg.src_start * sample_rate)
            e = min(int(seg.src_end   * sample_rate), total)
            pieces.append(audio[s:e])
        elif isinstance(seg, SynthSegment):
            pieces.append(np.zeros(int(seg.duration * sample_rate), dtype=np.float32))

    return np.concatenate(pieces) if pieces else np.array([], dtype=np.float32)


# ── Основной рендер ───────────────────────────────────────────────────────────

def render(session: ParsedSession, timeline: Timeline, out_path: Path,
           speech_volume: float = 5.0,
           camera_volume: float = 0.3,
           apply_hud: bool = True,
           debug: bool = False) -> None:
    """Смонтировать финальное видео по Timeline."""
    video_path = session.session_dir / "video.mp4"
    tmp_files: list[Path] = []

    probe_data = _probe(video_path)
    W, H  = probe_data["width"], probe_data["height"]
    fps   = probe_data["fps"]
    scale = W / 896.0
    print(f"Видео: {W}×{H}  fps={fps}")

    hud_telemetry = hud_turns = None
    if apply_hud:
        from .hud_pipe import TelemetryQuery, TurnQuery, TokenQuery, OverlayQuery, SpeechQuery, process_video_segment
        from pilot.config import robot_hud_title
        hud_telemetry = TelemetryQuery(session)
        hud_turns     = TurnQuery(session.events)
        hud_tokens    = TokenQuery(session.events)
        hud_overlays  = OverlayQuery(timeline)
        hud_speech    = SpeechQuery(timeline.audio_mixes)
        hud_title     = robot_hud_title(session.model)

    v_streams: list[ffmpeg.nodes.FilterableStream] = []

    cumulative_drift = 0.0  # используется только при debug=True
    zoom_num = 0
    for i, seg in enumerate(timeline.segments):
        if isinstance(seg, VideoSegment):
            if apply_hud:
                tmp = session.session_dir / f"_tmp_hud_{i:04d}.mp4"
                print(f"HUD [{seg.src_start:.1f}–{seg.src_end:.1f}s]…")
                process_video_segment(
                    seg, video_path, hud_telemetry, hud_turns,
                    timeline.filters, timeline.out_filters,
                    fps, scale, W, H, tmp,
                    hud_tokens, hud_title, hud_overlays,
                    speech_query=hud_speech if debug else None,
                    debug_overlay=debug,
                )
                if debug:
                    expected = seg.src_end - seg.src_start
                    vd, _ = _probe_duration(tmp)
                    delta = vd - expected
                    cumulative_drift += delta
                    print(f"  sync: expected={expected:.3f}s  video={vd:.3f}s  "
                          f"Δ={delta:+.3f}s  cumul={cumulative_drift:+.3f}s")
                tmp_files.append(tmp)
                src = ffmpeg.input(str(tmp))
                v_streams.append(src.video.filter("setpts", "PTS-STARTPTS"))
            else:
                src = ffmpeg.input(str(video_path), ss=seg.src_start, to=seg.src_end)
                v_streams.append(src.video.filter("setpts", "PTS-STARTPTS"))

        elif isinstance(seg, SynthSegment) and seg.kind == "zoom_animation":
            zoom_num += 1
            tmp = _render_zoom_segment(seg, session, video_path, zoom_num, W, H)
            tmp_files.append(tmp)
            src = ffmpeg.input(str(tmp))
            v_streams.append(src.video.filter("setpts", "PTS-STARTPTS"))

    if not v_streams:
        raise ValueError("Нет сегментов для рендера")

    # ── Concat видео ─────────────────────────────────────────────────────────
    n = len(v_streams)
    if n == 1:
        video_out = v_streams[0]
    else:
        concat_node = ffmpeg.concat(*v_streams, v=1, a=0, n=n).node
        video_out = concat_node[0]

    # ── Аудио в сэмпловом домене ──────────────────────────────────────────────
    print("Аудио…")
    audio_arr = build_audio(timeline, video_path, camera_volume, speech_volume)
    audio_tmp = out_path.with_name(out_path.stem + "_aud.wav")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "f32le", "-ar", "48000", "-ac", "1", "-i", "pipe:0",
            str(audio_tmp),
        ],
        input=audio_arr.tobytes(),
        stderr=subprocess.DEVNULL,
        check=True,
    )
    tmp_files.append(audio_tmp)

    # ── Вывод ────────────────────────────────────────────────────────────────
    audio_in = ffmpeg.input(str(audio_tmp)).audio
    (
        ffmpeg
        .output(
            video_out, audio_in, str(out_path),
            vcodec="libx264", crf=18, preset="fast",
            acodec="aac", **{"b:a": "192k"},
        )
        .overwrite_output()
        .run()
    )
    print(f"Готово: {out_path}")

    # ── Очистка temp файлов ───────────────────────────────────────────────────
    for tmp in tmp_files:
        tmp.unlink(missing_ok=True)
