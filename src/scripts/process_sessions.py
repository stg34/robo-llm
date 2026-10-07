"""
Пакетная обработка сессий: fix → timeline → report.

Использование:
    python scripts/process_sessions.py logs/brain_20260519*
    python scripts/process_sessions.py logs/brain_20260519_110030 logs/brain_20260519_154214

Шаги для каждой сессии:
  0. fix_session --add-model  — вставить/дополнить session_start моделью (если --add-model задан)
  1. fix_session              — исправление speech_start/speech_end
  2. visualize               — timeline.html + timeline.json
  3. log_to_md               — report.md

--add-model MODEL  вставить model в session_start (независимо от --no-fix)
--no-fix           пропустить fix_session (шаг 1)
--no-timeline      пропустить visualize
--no-report        пропустить log_to_md
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], label: str) -> bool:
    """Запустить команду, вернуть True если успешно."""
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  [ОШИБКА] {label} завершился с кодом {result.returncode}")
        return False
    return True


def process(session_dir: Path, add_model: str | None,
            fix: bool, timeline: bool, report: bool) -> None:
    print(f"\n── {session_dir.name} ──────────────────────────")

    if add_model:
        print("  [0] fix_session --add-model…")
        _run([sys.executable, "-m", "scripts.pipeline.fix_session",
              str(session_dir), "--add-model", add_model], "fix_session --add-model")

    if fix:
        print("  [1/3] fix_session…")
        _run([sys.executable, "-m", "scripts.pipeline.fix_session", str(session_dir)], "fix_session")

    if timeline:
        print("  [2/3] visualize…")
        _run([sys.executable, "-m", "scripts.pipeline.visualize", str(session_dir)], "visualize")

    if report:
        print("  [3/3] log_to_md…")
        _run([sys.executable, "scripts/log_to_md.py", str(session_dir)], "log_to_md")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Пакетная обработка сессий: fix → timeline → report"
    )
    p.add_argument("session_dirs", nargs="+", type=Path,
                   help="Одна или несколько папок сессий (glob поддерживается shell-ом)")
    p.add_argument("--add-model", metavar="MODEL",
                   help="Вставить/дополнить session_start полем model (независимо от --no-fix)")
    p.add_argument("--no-fix",      action="store_true", help="Пропустить fix_session")
    p.add_argument("--no-timeline", action="store_true", help="Пропустить visualize")
    p.add_argument("--no-report",   action="store_true", help="Пропустить log_to_md")
    args = p.parse_args()

    dirs = sorted(d.resolve() for d in args.session_dirs if d.is_dir())
    if not dirs:
        p.error("Не найдено ни одной папки сессии")

    print(f"Сессий: {len(dirs)}")
    for d in dirs:
        process(d,
                add_model=args.add_model,
                fix=not args.no_fix,
                timeline=not args.no_timeline,
                report=not args.no_report)

    print("\nГотово.")


if __name__ == "__main__":
    main()
