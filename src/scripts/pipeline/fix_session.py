"""
Инструмент исправления session.jsonl.

Три режима:

  --ask-human  (по умолчанию)
    Вставить speech_start/speech_end для ask_human вызовов, которые произнесли
    речь, но не залогировали события (старые сессии, до добавления filename в brain.py).
    Признак ghost: между tool_start ask_human и tool_end ask_human нет speech_start.

  --speech-map
    Полный override речевых событий по speech_map.json:
      - удаляет все speech_start / speech_end
      - вставляет корректные пары с точными таймингами из speech_map.json

  --add-model MODEL
    Добавить поле model в session_start (старые сессии до 2026-05-07).
    Если session_start отсутствует — вставить минимальный как первое событие.
    Если session_start уже содержит непустое поле model — ничего не делать.
    Сессии до 2026-05-06 (commit cc95435, multi-provider LLM) использовали
    исключительно claude-opus-4-6.

Оригинал сохраняется как session.jsonl.bak.

Использование:
  python -m scripts.pipeline.fix_session <session_dir>                        # --ask-human
  python -m scripts.pipeline.fix_session <session_dir> --speech-map
  python -m scripts.pipeline.fix_session <session_dir> --add-model claude-opus-4-6
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def _mp3_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], text=True)
    return float(out.strip())


def fix_ask_human_speech(session_dir: Path) -> None:
    """Вставить speech_start/speech_end для ghost ask_human вызовов."""
    jsonl_path  = session_dir / "session.jsonl"
    backup_path = session_dir / "session.jsonl.bak"

    if not jsonl_path.exists():
        raise FileNotFoundError(f"session.jsonl не найден: {jsonl_path}")

    raw_events: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_events.append(json.loads(line))

    new_events: list[dict] = []
    file_count = 0

    for i, e in enumerate(raw_events):
        if e.get("type") == "speech_start":
            file_count += 1
        elif e.get("type") == "tool_start" and e.get("tool") == "ask_human":
            question = e.get("input", {}).get("question", "")
            if not question:
                continue
            has_speech = False
            for ej in raw_events[i + 1:]:
                if ej.get("type") == "tool_end" and ej.get("tool") == "ask_human":
                    break
                if ej.get("type") == "speech_start":
                    has_speech = True
                    break
            if has_speech:
                continue

            file_count += 1
            filename = f"speech_{file_count:03d}.mp3"
            mp3_path = session_dir / filename
            if not mp3_path.exists():
                print(f"  ВНИМАНИЕ: {filename} не найден, пропускаю")
                continue

            dur  = _mp3_duration(mp3_path)
            ts   = e.get("ts", 0.0) + 0.001  # сразу после tool_start
            turn = e.get("turn", 0)
            new_events.append({"type": "speech_start", "ts": ts, "turn": turn,
                                "text": question, "filename": filename})
            new_events.append({"type": "speech_end", "ts": ts + dur, "turn": turn})
            print(f"  {filename}: turn={turn}  dur={dur:.2f}s  \"{question[:60]}\"")

    if not new_events:
        print("Нет ghost ask_human событий — сессия уже корректна.")
        return

    result = sorted(raw_events + new_events, key=lambda ev: ev.get("ts", 0.0))
    shutil.copy2(jsonl_path, backup_path)
    print(f"Бэкап: {backup_path}")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for ev in result:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(f"Готово: добавлено {len(new_events) // 2} пар событий → {jsonl_path}")


def fix_session(session_dir: Path) -> None:
    jsonl_path    = session_dir / "session.jsonl"
    map_path      = session_dir / "speech_map.json"
    backup_path   = session_dir / "session.jsonl.bak"

    if not jsonl_path.exists():
        raise FileNotFoundError(f"session.jsonl не найден: {jsonl_path}")
    if not map_path.exists():
        raise FileNotFoundError(f"speech_map.json не найден: {map_path}")

    speech_map: dict[str, float] = json.loads(map_path.read_text(encoding="utf-8"))

    raw_events: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_events.append(json.loads(line))

    # session_start_ts
    session_start_ts: float = 0.0
    for e in raw_events:
        if e.get("type") == "session_start":
            session_start_ts = e["ts"]
            break
    if session_start_ts == 0.0 and raw_events:
        session_start_ts = raw_events[0].get("ts", 0.0)

    # Удаляем старые речевые события
    n_removed = sum(1 for e in raw_events
                    if e.get("type") in ("speech_start", "speech_end"))
    filtered = [e for e in raw_events
                if e.get("type") not in ("speech_start", "speech_end")]

    # Строим новые пары из speech_map
    new_events: list[dict] = []
    for filename, session_relative_t in sorted(speech_map.items(), key=lambda kv: kv[1]):
        mp3 = session_dir / filename
        if not mp3.exists():
            print(f"  ВНИМАНИЕ: {filename} не найден, пропускаю")
            continue
        dur     = _mp3_duration(mp3)
        ts_wall = session_start_ts + session_relative_t
        new_events.append({
            "type":     "speech_start",
            "ts":       ts_wall,
            "filename": filename,
            "text":     "",
        })
        new_events.append({
            "type": "speech_end",
            "ts":   ts_wall + dur,
            "filename": filename,
        })
        print(f"  {filename}: start={session_relative_t:.3f}s  dur={dur:.2f}s")

    # Объединяем и сортируем по ts
    result = sorted(filtered + new_events, key=lambda e: e.get("ts", 0.0))

    # Бэкап
    shutil.copy2(jsonl_path, backup_path)
    print(f"Бэкап: {backup_path}")

    # Запись
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for e in result:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"Готово: удалено {n_removed}, добавлено {len(new_events)} событий → {jsonl_path}")


def fix_add_model(session_dir: Path, model: str) -> None:
    """Добавить поле model в session_start; вставить его если отсутствует."""
    jsonl_path  = session_dir / "session.jsonl"
    backup_path = session_dir / "session.jsonl.bak"

    if not jsonl_path.exists():
        raise FileNotFoundError(f"session.jsonl не найден: {jsonl_path}")

    raw_events: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_events.append(json.loads(line))

    # Найти существующий session_start
    start_idx = next(
        (i for i, e in enumerate(raw_events) if e.get("type") == "session_start"),
        None,
    )

    if start_idx is not None:
        existing_model = raw_events[start_idx].get("model", "")
        if existing_model:
            print(f"session_start уже содержит model={existing_model!r} — ничего не делаю.")
            return
        raw_events[start_idx]["model"] = model
        print(f"Добавлено поле model={model!r} в существующий session_start.")
    else:
        ts = raw_events[0].get("ts", 0.0) if raw_events else 0.0
        new_start = {"ts": ts, "type": "session_start", "model": model}
        raw_events.insert(0, new_start)
        print(f"Вставлен новый session_start (ts={ts}) с model={model!r}.")

    shutil.copy2(jsonl_path, backup_path)
    print(f"Бэкап: {backup_path}")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for e in raw_events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"Готово → {jsonl_path}")


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Исправить session.jsonl")
    p.add_argument("session_dir", type=Path)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--ask-human", dest="mode", action="store_const", const="ask_human",
                   help="Вставить speech события для ghost ask_human (по умолчанию)")
    g.add_argument("--speech-map", dest="mode", action="store_const", const="speech_map",
                   help="Полный override речи по speech_map.json")
    g.add_argument("--add-model", dest="add_model", metavar="MODEL",
                   help="Добавить поле model в session_start (старые сессии до 2026-05-07)")
    p.set_defaults(mode="ask_human")
    args = p.parse_args()
    sd = args.session_dir.resolve()
    if args.add_model:
        fix_add_model(sd, args.add_model)
    elif args.mode == "speech_map":
        fix_session(sd)
    else:
        fix_ask_human_speech(sd)


if __name__ == "__main__":
    main()
