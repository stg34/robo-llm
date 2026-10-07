"""
Индекс сессий логов.

Сканирует папку logs/, читает session.jsonl каждой сессии,
генерирует logs/INDEX.md со списком всех экспериментов.

Использование:
    python scripts/index_logs.py [logs_dir]
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _fmt_tokens(n: int) -> str:
    if not n:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}с"
    return f"{s // 60}м {s % 60}с"


def _parse_session(session_dir: Path) -> dict | None:
    log_file = session_dir / "session.jsonl"
    if not log_file.exists():
        return None

    decoder = json.JSONDecoder()
    events = []
    try:
        text = log_file.read_text(encoding="utf-8")
        pos = 0
        while pos < len(text):
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            if pos >= len(text):
                break
            try:
                obj, end = decoder.raw_decode(text, pos)
                if isinstance(obj, dict):
                    events.append(obj)
                pos = end
            except json.JSONDecodeError:
                pos += 1
    except Exception:
        return None

    if not events:
        return None

    session_start = next((e for e in events if e.get("type") == "session_start"), None)

    model = "—"
    temperature = None
    task = ""
    tool_names = []

    if session_start:
        model = session_start.get("model") or "—"
        temperature = session_start.get("temperature")
        task = session_start.get("task") or ""
        raw_tools = session_start.get("tools") or []
        if raw_tools and isinstance(raw_tools[0], dict):
            tool_names = [t["name"] for t in raw_tools]
        else:
            tool_names = [str(t) for t in raw_tools]

    # статистика
    turns = max((e.get("turn", 0) for e in events if "turn" in e), default=0)
    total_tokens = sum(
        e.get("usage", {}).get("input_tokens", 0) + e.get("usage", {}).get("output_tokens", 0)
        for e in events if e.get("type") == "api_response_end"
    )
    ts_list = [e["ts"] for e in events if "ts" in e]
    duration = (max(ts_list) - min(ts_list)) if len(ts_list) >= 2 else 0.0
    first_ts = min(ts_list) if ts_list else None

    # дата из ts или из имени папки
    if first_ts:
        dt = datetime.fromtimestamp(first_ts)
    else:
        name = session_dir.name  # brain_YYYYMMDD_HHMMSS...
        parts = name.split("_")
        try:
            dt = datetime.strptime(parts[1] + parts[2][:6], "%Y%m%d%H%M%S")
        except Exception:
            dt = None

    config_name = None
    if (session_dir / "experiment.yaml").exists():
        config_name = "experiment.yaml"

    video_file = session_dir / "result_titled.mp4"
    video_dt = None
    if video_file.exists():
        video_dt = datetime.fromtimestamp(video_file.stat().st_mtime)

    return {
        "name": session_dir.name,
        "dt": dt,
        "model": model,
        "temperature": temperature,
        "task": task,
        "tool_names": tool_names,
        "turns": turns,
        "total_tokens": total_tokens,
        "duration": duration,
        "config_name": config_name,
        "video_dt": video_dt,
    }


def build_index(logs_dir: Path) -> None:
    sessions = []
    for d in sorted(logs_dir.iterdir()):
        if d.is_dir() and d.name.startswith("brain_"):
            info = _parse_session(d)
            if info:
                sessions.append(info)

    # сортировка: новые сначала
    sessions.sort(key=lambda s: s["dt"] or datetime.min, reverse=True)

    lines = ["# Индекс сессий\n"]
    lines.append(f"Всего сессий: {len(sessions)}\n")

    for s in sessions:
        dt_str = s["dt"].strftime("%d.%m.%Y %H:%M") if s["dt"] else "—"
        temp_str = str(s["temperature"]) if s["temperature"] is not None else "—"
        tools_str = " · ".join(f"`{n}`" for n in s["tool_names"]) if s["tool_names"] else "—"

        lines.append(f"---\n")
        lines.append(f"## {s['name']}\n")
        lines.append(f"### Дата: {dt_str}")
        if s["task"]:
            lines.append(f"**Задача:** {s['task']}\n")
        if s["tool_names"]:
            lines.append(f"**Тулы:** {tools_str}\n")

        meta = [
            f"* **Модель:** {s['model']}",
            f"* **t°:** {temp_str}",
            f"* **Ходы:** {s['turns']}",
            f"* **Токены:** {_fmt_tokens(s['total_tokens'])}",
            f"* **Длит.:** {_fmt_duration(s['duration'])}",
        ]
        if s["config_name"]:
            meta.append(f"* **Конфиг:** {s['config_name']}")
        if s["video_dt"]:
            video_str = s["video_dt"].strftime("%d.%m.%Y %H:%M")
            meta.append(f"* **Видео:** ✅ result_titled.mp4 ({video_str})")
        else:
            meta.append(f"* **Видео:** ❌ нет")

        lines.append("\n".join(meta) + "\n")

    index_path = logs_dir / "INDEX.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Индекс: {index_path}  ({len(sessions)} сессий)")


if __name__ == "__main__":
    logs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs")
    if not logs_dir.exists():
        print(f"Ошибка: папка не найдена: {logs_dir}")
        sys.exit(1)
    build_index(logs_dir)
