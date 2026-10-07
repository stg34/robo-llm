"""
Загрузка конфига эксперимента из YAML-файла.

Формат файла:
    model: gemini-2.0-flash          # опционально, дефолт из .env
    task: "найди красный мяч"        # опционально, дефолт из CLI
    tools:                           # опционально, дефолт — все инструменты
      - move
      - turn_relative
      - zoom
      - done
    system_prompt: |                 # опционально, дефолт из brain.py
      Ты — бортовой AI...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from pilot.tools import ROBOT_TOOLS

ALL_TOOL_NAMES = {t["name"] for t in ROBOT_TOOLS}


@dataclass
class Experiment:
    model: str | None = None
    task: str | None = None
    tools: list[dict] | None = None    # None = все инструменты
    system_prompt: str | None = None   # None = дефолт из brain.py
    temperature: float | None = None   # None = дефолт провайдера
    system_image: Path | None = None   # путь к изображению (абсолютный или относительно конфига)


def load(path: str | Path) -> Experiment:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Конфиг не найден: {path}")

    if yaml is None:
        raise ImportError("Установите PyYAML: pip install pyyaml")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    exp = Experiment()

    if "model" in data:
        exp.model = str(data["model"])

    if "task" in data:
        exp.task = str(data["task"])

    if "system_prompt" in data:
        exp.system_prompt = str(data["system_prompt"])

    if "temperature" in data:
        exp.temperature = float(data["temperature"])

    if "system_image" in data:
        img_path = Path(str(data["system_image"]))
        if not img_path.is_absolute():
            img_path = path.parent / img_path
        if not img_path.exists():
            raise FileNotFoundError(f"system_image не найден: {img_path}")
        exp.system_image = img_path

    if "tools" in data:
        names = [str(n) for n in data["tools"]]
        unknown = set(names) - ALL_TOOL_NAMES
        if unknown:
            raise ValueError(f"Неизвестные инструменты в конфиге: {unknown}")
        exp.tools = [t for t in ROBOT_TOOLS if t["name"] in names]

    return exp
