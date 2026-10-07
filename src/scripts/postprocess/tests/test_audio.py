"""
Тесты для scripts/postprocess/audio.py — функция _collect_speech_files.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from scripts.postprocess.audio import _collect_speech_files


def _make_event(ts: float, vt: float, event_type: str, **kwargs) -> dict:
    return {"ts": ts, "vt": vt, "type": event_type, **kwargs}


def _make_mp3(session_dir: Path, name: str) -> Path:
    """Создать пустой фиктивный mp3-файл."""
    p = session_dir / name
    p.write_bytes(b"ID3")  # минимальный заголовок
    return p


class TestCollectSpeechFiles:
    def test_collect_speech_files_fallback(self):
        """Если speech_map.json нет — используется speech_start логика."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            _make_mp3(session_dir, "speech_001.mp3")
            _make_mp3(session_dir, "speech_002.mp3")

            timeline = [
                _make_event(ts=1000.0, vt=0.0,  event_type="session_start"),
                _make_event(ts=1010.0, vt=10.0, event_type="speech_start",
                            text="Первая фраза"),
                _make_event(ts=1020.0, vt=20.0, event_type="speech_start",
                            text="Вторая фраза"),
            ]

            result = _collect_speech_files(
                session_dir=session_dir,
                timeline=timeline,
                pauses=[],
                zoom_pause_map={},
                zoom_anim_sec=0.0,
            )

            assert len(result) == 2
            assert result[0][0].name == "speech_001.mp3"
            assert result[0][1] == pytest.approx(10.0)
            assert result[1][0].name == "speech_002.mp3"
            assert result[1][1] == pytest.approx(20.0)

    def test_collect_speech_files_speech_map(self):
        """Если speech_map.json существует, тайминги берутся из него."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            _make_mp3(session_dir, "speech_001.mp3")
            _make_mp3(session_dir, "speech_002.mp3")

            # Параметры синхронизации
            session_start_ts = 1000.0
            beep_ts_wall     = 1005.0   # бип через 5с от старта сессии
            video_beep_t     = 3.0      # бип в видео на 3.0с

            # speech_map: тайминги от начала сессии
            # speech_001: 12.0s от старта → vt = 12.0 - (1005-1000) + 3.0 = 10.0
            # speech_002: 17.0s от старта → vt = 17.0 - 5.0 + 3.0 = 15.0
            speech_map = {
                "speech_001.mp3": 12.0,
                "speech_002.mp3": 17.0,
            }
            (session_dir / "speech_map.json").write_text(
                json.dumps(speech_map), encoding="utf-8"
            )

            # timeline может не содержать speech_start — карта их заменяет
            timeline = [
                _make_event(ts=1000.0, vt=0.0, event_type="session_start"),
            ]

            result = _collect_speech_files(
                session_dir=session_dir,
                timeline=timeline,
                pauses=[],
                zoom_pause_map={},
                zoom_anim_sec=0.0,
                beep_ts_wall=beep_ts_wall,
                video_beep_t=video_beep_t,
                session_start_ts=session_start_ts,
            )

            assert len(result) == 2
            assert result[0][0].name == "speech_001.mp3"
            assert result[0][1] == pytest.approx(10.0)
            assert result[1][0].name == "speech_002.mp3"
            assert result[1][1] == pytest.approx(15.0)

    def test_collect_speech_files_speech_map_sorted_by_time(self):
        """Файлы из speech_map.json сортируются по времени, не по имени."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            _make_mp3(session_dir, "speech_001.mp3")
            _make_mp3(session_dir, "speech_002.mp3")

            session_start_ts = 1000.0
            beep_ts_wall     = 1000.0
            video_beep_t     = 0.0

            # speech_002 идёт раньше speech_001 по времени в карте
            speech_map = {
                "speech_001.mp3": 20.0,
                "speech_002.mp3": 5.0,
            }
            (session_dir / "speech_map.json").write_text(
                json.dumps(speech_map), encoding="utf-8"
            )

            result = _collect_speech_files(
                session_dir=session_dir,
                timeline=[],
                pauses=[],
                zoom_pause_map={},
                zoom_anim_sec=0.0,
                beep_ts_wall=beep_ts_wall,
                video_beep_t=video_beep_t,
                session_start_ts=session_start_ts,
            )

            # speech_002 должен быть первым (vt=5.0 < vt=20.0)
            assert result[0][0].name == "speech_002.mp3"
            assert result[1][0].name == "speech_001.mp3"

    def test_collect_speech_files_zoom_offset(self):
        """zoom_added_before корректно сдвигает позицию речи."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            _make_mp3(session_dir, "speech_001.mp3")

            # Одна zoom-анимация длиной 2.0s вставлена до момента 8.0
            # (пауза началась в 5.0 — раньше vt речи 10.0)
            pauses = [(5.0, 8.0)]
            zoom_pause_map = {0: {"zoom_num": 1, "cx_ai": 0, "cy_ai": 0, "vt_end": 4.9}}
            zoom_anim_sec = 2.0

            timeline = [
                _make_event(ts=1000.0, vt=0.0,  event_type="session_start"),
                _make_event(ts=1010.0, vt=10.0, event_type="speech_start",
                            text="Фраза"),
            ]

            result = _collect_speech_files(
                session_dir=session_dir,
                timeline=timeline,
                pauses=pauses,
                zoom_pause_map=zoom_pause_map,
                zoom_anim_sec=zoom_anim_sec,
            )

            assert len(result) == 1
            mp3_path, out_vt = result[0]
            # vt=10.0, cut_before(10.0) = 3.0 (пауза 5-8), zoom_added=2.0
            # out_vt = 10.0 - 3.0 + 2.0 = 9.0
            assert out_vt == pytest.approx(9.0)

    def test_speech_on_pause_boundary_is_clamped(self):
        """Речь на границе паузы (floating-point) сдвигается на конец паузы, не пропускается."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            _make_mp3(session_dir, "speech_001.mp3")

            # speech vt чуть меньше pe из-за округления
            pauses = [(5.0, 15.0)]
            timeline = [
                _make_event(ts=1014.99, vt=14.99, event_type="speech_start",
                            text="Фраза на границе паузы"),
            ]

            result = _collect_speech_files(
                session_dir=session_dir,
                timeline=timeline,
                pauses=pauses,
                zoom_pause_map={},
                zoom_anim_sec=0.0,
            )

            # Сдвигается на pe=15.0, cut_before(15.0)=10.0 → out_vt=5.0
            assert len(result) == 1
            assert result[0][1] == pytest.approx(5.0)
