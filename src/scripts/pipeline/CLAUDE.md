# Постобработка видео — scripts/pipeline/

Новый пакет постобработки сессий робота. Заменяет `scripts/postprocess/`.

```bash
# Полный рендер
python -m scripts.pipeline <session_dir> [--beep-t T] [--no-cut] [--no-hud] [--no-slides]
    [--speech-volume N] [--camera-volume N] [--final] [--debug] [--out PATH]

# Дамп кривой HUD alpha вокруг зум-переходов (диагностика)
python -m scripts.pipeline.debug_alpha <session_dir> [--beep-t T] [--window 2.0] [--verbose]

# Визуализация тайминга (Layer 1+2)
python -m scripts.pipeline.visualize <session_dir> --beep-t 3.45

# Исправление лога по speech_map.json (разовая операция)
python -m scripts.pipeline.fix_session <session_dir>
```

## Архитектура: три слоя

```
session.jsonl          →  Layer 1 (parser.py)    →  ParsedSession
telemetry.jsonl           state intervals              actors: {ai, robot, speech, operator}

ParsedSession          →  Layer 2 (rules.py)     →  list[Operation]
                          rule engine                  Cut / Insert / Filter / MixAudio / Overlay

ParsedSession          →  Layer 3a (timeline.py) →  Timeline
+ list[Operation]         сегменты + out_t             VideoSegment / SynthSegment

Timeline               →  Layer 3b (render.py)   →  result.mp4
+ ParsedSession           ffmpeg-python граф           concat + amix TTS + HUD overlay
```

### Два пространства времени

- `src_t` — оригинальное время видеофайла (секунды от начала)
- `out_t` — выходное время после Cut/Insert (вычисляется в Layer 3a)

Якорь синхронизации: `sync_beep` в `session.jsonl` содержит `ts_wall` бипа;
бип найден в аудио → `video_beep_t`. Перевод: `src_t = (ts_wall - beep_ts_wall) + video_beep_t`.

---

## Layer 1 — parser.py

**Вход:** `session_dir`, `video_beep_t`, `duration`  
**Выход:** `ParsedSession`

```python
session = parse_session(session_dir, video_beep_t=3.45, duration=163.4)
session.actors   # dict: actor → list[StateInterval]
session.events   # все события с добавленным src_t
```

### Акторы и состояния

| Актор      | Состояния                                                          |
|------------|--------------------------------------------------------------------|
| `ai`       | `idle` · `thinking`                                                |
| `robot`    | `standing` · `moving` · `turning` · `zooming` · `shooting` · `sleeping` · `stopping` · `done` · `dying` |
| `speech`   | `silent` · `speaking`                                              |
| `operator` | `idle` · `asking` · `answered` · `waiting_msg`                    |

**Derived states** (постпроцессинг поверх базовых):
- `robot=pre_zoom` — окно `pre_zoom_sec=1.0s` до начала зума
- `robot=post_zoom` — окно `post_zoom_sec=1.0s` после зума

### StateInterval

```python
@dataclass
class StateInterval:
    src_start: float
    src_end:   float
    state:     str
    meta:      dict   # текст речи, zoom coords, meters, seconds, summary...
```

### speech_map.json

Если в `session_dir` лежит `speech_map.json` — используй `fix_session.py` для
исправления лога перед парсингом. Формат: `{"speech_001.mp3": 12.68, ...}` (секунды от session_start).

---

## Layer 2 — rules.py

**Вход:** `ParsedSession`  
**Выход:** `list[Operation]`

```python
ops = apply_rules(session)
```

### Операции

| Класс       | Поля                                      | Смысл                              |
|-------------|-------------------------------------------|------------------------------------|
| `Cut`       | `src_start`, `src_end`                    | Вырезать интервал                  |
| `Insert`    | `src_t`, `kind`, `anchor`, `meta`         | Вставить синтетический сегмент     |
| `Filter`    | `src_start`, `src_end`, `effect`, `meta`  | Видеофильтр на интервале (src_t)   |
| `MixAudio`  | `src_t`, `path`, `meta`                   | Подмешать аудиофайл                |
| `Overlay`   | `src_start`, `src_end`, `kind`, `meta`    | Информационный оверлей             |

`Insert.anchor`:
- `"at"` — вставить точно в `src_t`
- `"after_next_cut"` — Layer 3 привяжет к ближайшему Cut после `src_t`

### Правила (из коробки)

```
ai=thinking + robot=standing   →  Cut
robot=pre_zoom                 →  Filter(fade_hud_out, src_t)   ← FADE_OUT_SEC перед концом окна
robot=zooming                  →  Insert(zoom_animation, anchor=after_next_cut)
speech=speaking                →  MixAudio(speech_NNN.mp3)
operator=answered              →  Overlay(operator_answer)
operator=waiting_msg           →  Overlay(operator_initiative)
```

**Константы длительности фейдов** (`rules.py`):
```python
FADE_OUT_SEC = 0.8   # длительность fade_hud_out перед зум-анимацией
FADE_IN_SEC  = 0.8   # длительность fade_hud_in после зум-анимации
```

Добавить правило: написать функцию `@rule("actor")` в `rules.py`. Engine не трогать.

### Единый источник инструментов

`pilot/tools.py` содержит `TOOL_STATES` — мета для парсера:
```python
TOOL_STATES = {
    "move":      {"robot_state": "moving",  "terminal": False, "meta_keys": ["meters"]},
    "done":      {"robot_state": "done",    "terminal": True,  "meta_keys": ["summary"]},
    ...
}
```
Добавить инструмент = обновить `TOOL_STATES` + `ROBOT_TOOLS` в `pilot/tools.py`.
Неизвестный инструмент в логе → `ValueError` с подсказкой.

---

## Layer 3a — timeline.py

**Вход:** `ParsedSession + list[Operation]`  
**Выход:** `Timeline` — список сегментов с `out_start`

```python
timeline = build_timeline(session, ops, apply_cuts=True)
timeline.segments      # list[VideoSegment | SynthSegment]
timeline.audio_mixes   # [(path, out_t), ...]
timeline.filters       # list[Filter] — для hud_alpha (src_t)
timeline.out_filters   # list[(effect, out_start, out_end)] — fade_hud_in в out_t
```

`out_filters` содержит **только** `fade_hud_in` после каждого SynthSegment.
`fade_hud_out` живёт в `timeline.filters` (src_t) через правило `pre_zoom_fade`.

---

## Layer 3b — render.py + hud_pipe.py

### process_video_segment (hud_pipe.py)

Для каждого `VideoSegment` запускает два ffmpeg-процесса:
- **reader**: `-ss src_start -to src_end` → rawvideo → pipe
- **writer**: pipe + `-ss src_start -to src_end -c:a copy` → HUD-обработанный mp4

**Важно**: `src_t` и `out_t` зажаты по `seg.src_end` / `seg.out_end`:
```python
src_t = min(seg.src_start + frame_idx / fps, seg.src_end)
out_t = min(seg.out_start + frame_idx / fps, seg.out_end)
```
Без этого лишние кадры от ffmpeg получают `alpha=1.0` и вызывают вспышку HUD.

### hud_alpha (hud_pipe.py)

Приоритет при вычислении прозрачности HUD:
1. `out_filters` (out_t) — `fade_hud_in` после SynthSegment
2. `timeline.filters` (src_t) — `fade_hud_out` перед зумом
3. Default: `1.0`

```python
alpha = hud_alpha(timeline.filters, timeline.out_filters, src_t, out_t)
```

### --debug флаг

- Stdout: expected/video/audio длительность по каждому сегменту (A/V sync лог)
- На кадре: `src=X.XXX  out=X.XXX` внизу по центру (зелёный текст)

---

## Visualizer — visualize.py

Plotly HTML + JSON диаграмма тайминга. Показывает Layer 1 + Layer 2 в `src_t`.

```bash
python -m scripts.pipeline.visualize <session_dir> --beep-t 3.45 [--duration 120] [--out path.html]
# Выход: timeline.html (интерактивный) + timeline.json (для отладки с Claude)
```

- Ось Y — `src_t` (сверху вниз), ось X — акторы
- Цветные полосы по состояниям, красные зоны Cut, зелёные пунктиры Insert
- Hover с деталями, tooltip речи с переносом по словам

---

## debug_alpha.py

Дамп кривой HUD alpha вокруг зум-переходов. Использовать для диагностики вспышек и
неправильных переходов без рендера.

```bash
python -m scripts.pipeline.debug_alpha <session_dir> [--beep-t T] [--window 2.0] [--verbose]
```

Вывод:
- Таблица сегментов и фильтров
- `=== out_filters ===` и `=== timeline.filters ===` (с пометкой "НЕ активны" у игнорируемых)
- Alpha trace вокруг каждого SynthSegment: колонки `out_t`, `src_t`, `alpha`, `(src_f)`
- По умолчанию плоские зоны `alpha=1.0` сворачиваются; `--verbose` показывает все кадры

---

## Известные риски рассинхронизации (A/V sync)

### 1. A/V дрейф внутри `process_video_segment` (главный)

Два независимых ffmpeg-seek на одно `-ss`/`-to` окно. ffmpeg не гарантирует одинаковое
количество аудио/видео семплов. `-shortest` обрезает по короткой дорожке — дрейф ±кадры
на сегмент, суммарно заметен при 10+ сегментах.

### 2. Float-накопление

`src_t = seg.src_start + frame_idx / fps` — при нецелом fps накапливается ошибка.
Для HUD-виджетов некритично; зажато по `seg.src_end`.

### 3. MixAudio позиции

`out_t` для речи Polly из теоретического `Timeline`, реальные сегменты могут отличаться
по длительности → речь уезжает. Диагностика: `--debug`.

### Обсуждаемые улучшения (не реализованы)

- **Slice render**: `render(..., out_start, out_end)` — добавить `-ss`/`-to` к финальному
  ffmpeg output, не менять обработку сегментов. Полный рендер = `slice(0, total_duration)`.
- **Кадровый подход**: считать точное число кадров N → аудио = `round(N * 48000 / fps)` семплов,
  убрать `-shortest`. Устраняет A/V дрейф по источнику, а не по симптому.

### Известный архитектурный риск: новые Insert-типы с fade-парой

`build_timeline` step 5 жёстко проверяет `ins.kind == "zoom_animation"` при генерации `out_filters`.
Добавление нового Insert-типа с аналогичной парой fade_out/fade_in потребует явного добавления
его kind в эту проверку — легко забыть.

**Предлагаемый фикс (когда появится второй случай):** добавить `fade_in_sec: float = 0.0` в
`Insert`, убрать проверку по kind из `build_timeline`. Правило zoom_animation передаёт
`fade_in_sec=FADE_IN_SEC`; step 5 применяет fade_hud_in для любого insert с `fade_in_sec > 0`.

---

## fix_session.py

Разовый инструмент исправления лога когда `speech_start`/`speech_end` пропущены:

```bash
python -m scripts.pipeline.fix_session <session_dir>
# Читает speech_map.json, исправляет session.jsonl, сохраняет session.jsonl.bak
```
