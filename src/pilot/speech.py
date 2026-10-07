"""
Speech: синтез речи через Amazon Polly + воспроизведение через ffplay.

Файлы сохраняются в директорию сессии: speech_001.mp3, speech_002.mp3, ...
Воспроизведение синхронное — блокирует до окончания.
"""
import subprocess
from contextlib import closing
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class Speech:
    def __init__(self, region: str = "eu-west-1", voice_id: str = "Maxim"):
        self._session_dir: Path | None = None
        self._polly = boto3.Session().client("polly", region_name=region)
        self._voice_id = voice_id
        self._count = 0

    def set_session_dir(self, session_dir: Path) -> None:
        self._session_dir = session_dir

    def next_path(self) -> Path:
        """Путь следующего mp3-файла (до вызова say)."""
        save_dir = self._session_dir or Path(__file__).parent.parent / "logs"
        return save_dir / f"speech_{self._count + 1:03d}.mp3"

    def say(self, text: str) -> Path | None:
        """Синтезировать текст и воспроизвести синхронно через ffplay.

        Возвращает путь к сохранённому mp3 или None при ошибке.
        """
        text = text.strip()
        if not text:
            return None

        self._count += 1

        try:
            response = self._polly.synthesize_speech(
                Text=text,
                OutputFormat="mp3",
                VoiceId=self._voice_id,
            )
        except (BotoCoreError, ClientError) as e:
            print(f"[speech] Polly error: {e}")
            return None

        if "AudioStream" not in response:
            print("[speech] No audio stream from Polly")
            return None

        save_dir = self._session_dir or Path(__file__).parent.parent / "logs"
        save_dir.mkdir(parents=True, exist_ok=True)
        mp3_path = save_dir / f"speech_{self._count:03d}.mp3"

        with closing(response["AudioStream"]) as stream:
            mp3_path.write_bytes(stream.read())

        print(f"[speech] {mp3_path.name}: {text[:60]}{'...' if len(text) > 60 else ''}")
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(mp3_path)],
            check=False,
        )
        return mp3_path
