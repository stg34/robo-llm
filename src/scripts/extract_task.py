"""
Извлекает task из session.jsonl и пишет рядом task.txt.

Использование:
    python3 scripts/extract_task.py article/sessions/.../brain_TIMESTAMP/

Создаёт: <session_dir>/task.txt
"""
import json
import sys
from pathlib import Path


def extract_task(session_dir: Path) -> None:
    jsonl = session_dir / "session.jsonl"
    if not jsonl.exists():
        print(f"skip (нет session.jsonl): {session_dir}")
        return

    # Первая строка JSONL, но старые логи бывают pretty-printed
    # (один объект на несколько строк) — берём первый JSON-объект из текста.
    text = jsonl.read_text(encoding="utf-8").lstrip()
    try:
        first, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        print(f"skip (битый JSON): {session_dir}")
        return

    task = first.get("task")
    if not task:
        print(f"skip (нет task): {session_dir}")
        return

    out = session_dir / "task.txt"
    out.write_text(task + "\n", encoding="utf-8")
    print(f"ok: {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Использование: python3 scripts/extract_task.py <session_dir>")
    extract_task(Path(sys.argv[1]))
