"""
Конвертер лога сессии brain в Markdown-отчёт с картинками.

Использование:
    python3 scripts/log_to_md.py logs/brain_TIMESTAMP/

Создаёт: logs/brain_TIMESTAMP/report.md
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from pilot.tools import ROBOT_TOOLS
    _DEFAULT_TOOL_NAMES = [t["name"] for t in ROBOT_TOOLS]
except ImportError:
    _DEFAULT_TOOL_NAMES = []

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _fmt_date(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    return f"{dt.day} {MONTHS_RU[dt.month - 1]} {dt.year}, {dt.strftime('%H:%M')}"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m} мин {s} с" if m else f"{s} с"


def _tool_header(name: str, inp: dict) -> str:
    if name == "move":
        return f"**→ `move`** ({inp.get('meters', '?')} м)"
    if name == "turn_relative":
        deg = float(inp.get("degrees", 0))
        arrow = "↻" if deg > 0 else "↺"
        return f"**→ `turn_relative`** ({arrow} {abs(deg):.0f}°)"
    if name == "zoom":
        return f"**→ `zoom`** (cx={inp.get('cx', '?')}, cy={inp.get('cy', '?')})"
    if name == "ask_human":
        return "**→ `ask_human`**"
    if name == "sleep":
        return f"**→ `sleep`** ({inp.get('seconds', '?')} с)"
    if name == "shoot":
        return "**→ `shoot`**"
    if name == "stop":
        return "**→ `stop`**"
    if name == "done":
        return "**→ `done`**"
    args = ", ".join(f"{k}={v}" for k, v in inp.items())
    return f"**→ `{name}`**({args})"


def _tool_result(name: str, result: dict) -> str:
    clean = {k: v for k, v in result.items() if k != "image_b64"}
    if name == "move":
        status = clean.get("status", "?")
        moved = clean.get("moved_mm")
        s = f"← `{status}`"
        if moved is not None:
            s += f", проехал {moved} мм"
        return s
    if name == "turn_relative":
        status = clean.get("status", "?")
        rotated = clean.get("rotated_deg")
        s = f"← `{status}`"
        if rotated is not None:
            s += f", повернул {rotated:.1f}°"
        return s
    if name in ("done", "zoom", "ask_human"):
        return ""
    return f"← `{json.dumps(clean, ensure_ascii=False)}`"


def convert(session_dir: Path) -> None:
    log_file = session_dir / "session.jsonl"
    if not log_file.exists():
        print(f"Ошибка: {log_file} не найден")
        sys.exit(1)

    events = []
    decoder = json.JSONDecoder()
    with open(log_file, encoding="utf-8") as f:
        text = f.read()
    pos = 0
    while pos < len(text):
        # пропускаем пробелы/переносы между объектами
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

    if not events:
        print("Лог пустой")
        sys.exit(1)

    # ── Метаданные сессии ────────────────────────────────────────────────────
    session_start = next((e for e in events if e["type"] == "session_start"), None)
    first_ts = events[0]["ts"]
    last_ts  = events[-1]["ts"]

    if session_start:
        model       = session_start.get("model", "—")
        temperature = session_start.get("temperature")
        task        = session_start.get("task", "")
        raw_tools   = session_start.get("tools", None)
        if isinstance(raw_tools, list) and raw_tools:
            # новый формат: полные схемы; старый формат: список строк
            if isinstance(raw_tools[0], dict):
                tool_names = [t["name"] for t in raw_tools]
            else:
                tool_names = [str(t) for t in raw_tools]
        else:
            tool_names = _DEFAULT_TOOL_NAMES
    else:
        model, temperature, task = "—", None, ""
        tool_names = _DEFAULT_TOOL_NAMES

    # Fallback задачи для старых логов
    if not task:
        for e in events:
            if e["type"] == "api_request_start":
                for ln in e.get("telemetry", "").splitlines():
                    if ln.startswith("Задача:"):
                        task = ln.replace("Задача:", "").strip()
                        break
                if task:
                    break

    # ── Разбираем события по виткам ─────────────────────────────────────────
    turns: dict[int, dict] = {}

    def get_turn(n: int) -> dict:
        if n not in turns:
            turns[n] = {
                "frame": None,
                "telemetry": "",
                "text_blocks": [],
                "tools": [],
                "operator_msg": None,
                "usage": None,
            }
        return turns[n]

    open_tools: list[dict] = []   # tool_start без tool_end
    zoom_count  = 0
    total_tokens = 0

    for e in events:
        etype  = e["type"]
        turn_n = e.get("turn", 0)

        if etype == "api_request_start":
            tr = get_turn(turn_n)
            tr["frame"]     = e.get("frame")
            tr["telemetry"] = e.get("telemetry", "")

        elif etype == "api_response_end":
            tr = get_turn(turn_n)
            for block in e.get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    tr["text_blocks"].append(block["text"].strip())
            usage = e.get("usage")
            if usage:
                tr["usage"] = usage
                total_tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

        elif etype == "operator_message":
            get_turn(turn_n)["operator_msg"] = e.get("text", "")

        elif etype == "tool_start":
            open_tools.append(e)

        elif etype == "tool_end":
            # Ищем последний незакрытый tool_start с тем же turn + tool
            start = None
            for i in range(len(open_tools) - 1, -1, -1):
                if (open_tools[i].get("turn") == turn_n and
                        open_tools[i].get("tool") == e.get("tool")):
                    start = open_tools.pop(i)
                    break

            tool_name   = e.get("tool", "")
            tool_input  = start.get("input", {}) if start else {}
            tool_result = e.get("result", {})

            zoom_img = zoom_num = None
            if tool_name == "zoom":
                zoom_count += 1
                zoom_num = zoom_count
                candidate = f"zoom_{zoom_count:03d}.jpg"
                zoom_img = candidate if (session_dir / candidate).exists() else None

            get_turn(turn_n)["tools"].append({
                "name":     tool_name,
                "input":    tool_input,
                "result":   tool_result,
                "zoom_img": zoom_img,
                "zoom_num": zoom_num,
            })

    # ── Формируем Markdown ───────────────────────────────────────────────────
    out: list[str] = []

    def ln(s: str = "") -> None:
        out.append(s)

    session_name = session_dir.name
    ln(f"# {session_name}")
    ln()
    ln(f"| | |")
    ln(f"|---|---|")
    ln(f"| **Дата** | {_fmt_date(first_ts)} |")
    ln(f"| **Модель** | `{model}` |")
    temp_str = str(temperature) if temperature is not None else "дефолт провайдера"
    ln(f"| **Температура** | {temp_str} |")
    if task:
        ln(f"| **Задача** | {task} |")
    if tool_names:
        ln(f"| **Инструменты** | {' · '.join(f'`{n}`' for n in tool_names)} |")
    real_turns = sorted(t for t in turns if t > 0)
    duration   = last_ts - first_ts
    stats_parts = [f"{len(real_turns)} витков", _fmt_duration(duration)]
    if total_tokens:
        stats_parts.append(f"{_fmt_tokens(total_tokens)} токенов")
    ln(f"| **Итого** | {', '.join(stats_parts)} |")
    ln()

    for turn_num in real_turns:
        tr = turns[turn_num]
        ln("---")
        ln()
        ln(f"## Виток {turn_num}")
        ln()

        # Кадр камеры
        if tr["frame"] and (session_dir / tr["frame"]).exists():
            ln(f"![Кадр {turn_num}]({tr['frame']})")
            ln()

        # Телеметрия
        if tr["telemetry"]:
            first_line = tr["telemetry"].splitlines()[0]
            ln(f"**{first_line}**")
            ln()

        # Сообщение оператора (--interactive или клавиша i)
        if tr["operator_msg"]:
            ln(f"> 💬 **Оператор →** {tr['operator_msg']}")
            ln()

        # Ответ ИИ
        for text in tr["text_blocks"]:
            for para in text.split("\n"):
                ln(f"> {para}" if para.strip() else ">")
            ln()

        # Токены витка
        if tr["usage"]:
            u   = tr["usage"]
            inp = u.get("input_tokens", 0)
            out_t = u.get("output_tokens", 0)
            ln(f"*↑ {inp} вх. / ↓ {out_t} исх. токенов*")
            ln()

        # Инструменты
        for tool in tr["tools"]:
            name     = tool["name"]
            inp      = tool["input"]
            result   = tool["result"]
            zoom_img = tool["zoom_img"]
            zoom_num = tool["zoom_num"]

            ln(_tool_header(name, inp))
            ln()

            if name == "zoom":
                if zoom_img:
                    ln(f"![Зум {zoom_num}]({zoom_img})")
                else:
                    actual = result.get("actual_center", "")
                    ln(f"*(изображение не найдено, центр: {actual})*")
                ln()

            elif name == "ask_human":
                question = inp.get("question", "")
                answer   = result.get("answer", "")
                if question:
                    ln(f"> ❓ {question}")
                if answer:
                    ln(f"> 💬 **Оператор:** {answer}")
                ln()

            elif name == "done":
                summary = inp.get("summary", "")
                if summary:
                    ln(f"> ✅ {summary}")
                ln()

            else:
                res_str = _tool_result(name, result)
                if res_str:
                    ln(res_str)
                ln()

    report_path = session_dir / "report.md"
    report_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Отчёт: {report_path}")
    print(f"Витков: {len(real_turns)}  |  "
          f"Токенов: {_fmt_tokens(total_tokens) if total_tokens else '—'}  |  "
          f"Длительность: {_fmt_duration(duration)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python3 scripts/log_to_md.py logs/brain_TIMESTAMP/")
        sys.exit(1)
    convert(Path(sys.argv[1]))
