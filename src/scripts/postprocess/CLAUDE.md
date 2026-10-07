# Постобработка видео — scripts/postprocess/

Пакет превращает сырую запись сессии робота в смонтированное видео с HUD, вырезанными паузами, голосом Polly и титровыми слайдами.

```
python -m scripts.postprocess <session_dir> [--lag SEC] [--no-cut] [--beep-t SEC]
                                             [--skip-render] [--speech-volume N] [--camera-volume N]
```

## Структура пакета

```
__main__.py          # точка входа: аргументы, основной pipeline
timeline.py          # загрузка и разбор событий сессии
render.py            # HUD-наложение на кадры (применяет виджеты из pilot/widgets/)
audio.py             # нарезка аудио, наложение речи Polly
zoom.py              # Ken Burns zoom-анимация
slides.py            # рендер титровых слайдов, склейка через ffmpeg concat
visualize_timeline.py  # HTML-диаграмма тайминга (отладка)
```

## Файлы сессии

| Файл | Описание |
|------|----------|
| `session.jsonl` | события сессии (см. ниже) |
| `telemetry.jsonl` | телеметрия ~10 Гц (ts, voltage, range_mm, speeds, currents, mag_x/y) |
| `video.mp4` | RTSP-запись с аудио (ffmpeg, `-c:v copy -c:a aac`) |
| `speech_NNN.mp3` | файлы Polly TTS по одному на фразу |
| `speech_map.json` | опциональный override тайминга речи: `{"speech_001.mp3": 12.68, ...}` (секунды от начала сессии) |
| `slides.json` | титровые слайды (start/finish), автогенерируется при старте pilot |
| `zoom_NNN.jpg` | кадр результата tool zoom (для анимации) |

## События session.jsonl и их влияние на видео

Все события хранятся с полем `ts` (Unix timestamp). `build_timeline()` конвертирует в `vt` (видео-время):

```
vt = (ts_wall - beep_ts_wall) + video_beep_t
```

### Синхронизация
- **`sync_beep`** — содержит `ts_wall` момента бипа. Бип ищется в аудиодорожке видео (`find_beep_position`). Это якорная точка синхронизации лога и видео.

### Паузы — вырезаются из видео
- **`api_request_start`** → **`api_response_end`** — Claude думает. Интервал вырезается из видео и аудио (если не `--no-cut`). Пока пауза — кадры пропускаются, аудио нарезается через PCM (f32le → numpy → aac pipe, чтобы избежать non-monotonic DTS).

### Zoom-анимация
- **`tool_start zoom`** / **`tool_end zoom`** — `build_zoom_pause_map()` находит ближайшую паузу ПОСЛЕ tool_end и привязывает к ней zoom-анимацию. Вместо тишины паузы вставляются 4 фазы Ken Burns (~3.2s при 15fps):
  1. Прицел (рамка + перекрестие)
  2. Наезд (crop src_frame → tight box, smoothstep)
  3. Удержание (zoom_result из zoom_NNN.jpg, метка "ZOOM x2")
  4. Отъезд (обратный Ken Burns: crop расширяется до полного кадра)
- В аудио на позиции zoom-анимации вставляется тишина той же длины.

### Речь Polly
- **`speech_start`** / **`speech_end`** — TTS-фразы. `speech_NNN.mp3` накладываются через `adelay` + `amix=normalize=0`. Тайминги из `speech_map.json` (если есть) или из `speech_start` событий timeline.
- Перевод session-relative времени в video-time: `vt = sess_t - (beep_ts_wall - session_start_ts) + video_beep_t`, затем вычитается длина вырезанных пауз + прибавляется длина zoom-анимаций до этой точки.

### HUD — состояние в каждом кадре
- **`tool_start move/turn_relative`** → метка "ЕДЕТ" / "ПОВОРОТ"
- **`tool_start done`** → метка "ГОТОВО"
- **`api_request_start`** → метка "ДУМАЕТ" (кадр всё равно вырезается, но если `--no-cut` — будет видно)
- **`speech_start`** → метка "ГОВОРИТ", субтитр с текстом фразы
- Телеметрия (voltage, heading, range, turn, tokens) — из ближайшего пакета `telemetry.jsonl` по wall-clock времени.

### Оверлей оператора
- **`tool_start ask_human`** → **`tool_end ask_human`**: робот спросил оператора голосом. Оверлей **"ОТВЕТ ОПЕРАТОРА"** показывается с `speech_end` (после того как вопрос дозвучал) до `tool_end`. Текст берётся из `result.answer` в `tool_end`.
- **`operator_message`** (перед `api_request_start`, без промежуточного `ask_human`) — оператор сам взял инициативу в `-i` режиме. Оверлей **"ОПЕРАТОР"** показывается на idle-периоде от предыдущего `tool_end`/`api_response_end` до `operator_message`.

### Токены
- **`api_response_end`** содержит `usage: {input_tokens, output_tokens}`. `get_tokens_at()` накапливает сумму и отображает в HUD строкой "Tokens: 12.3k".

## Ключевые технические решения

**PCM-подход для аудио** (`audio.py/_cut_audio`): ffmpeg извлекает срезанное аудио как raw f32le → numpy вставляет тишину в позиции zoom-анимаций → ffmpeg кодирует обратно в AAC через pipe. Это надёжнее filter_complex (aselect+asplit+concat), который давал non-monotonic DTS на коротких сегментах.

**anullsrc r=48000 в slides.py**: тихая аудиодорожка слайдов должна совпадать по sample_rate с основным видео (48kHz). Иначе ffmpeg concat demuxer растягивает аудио в 6× (48kHz интерпретируется как 8kHz).

**Zoom к паузе, а не к tool_end**: zoom-анимация вставляется в ближайшую паузу после зума, а не сразу после tool_end. Это потому что между tool_end zoom и следующей паузой может быть другой код (operator_message, короткое ожидание).

## Pipeline (порядок шагов в __main__.py)

1. Найти бип в аудио → `video_beep_t`
2. `build_timeline` → `build_pause_intervals` → `build_zoom_pause_map`
3. Рендер кадров в `result_noaudio.mp4` (многопоточно, ThreadPoolExecutor): пропускать паузы, вставлять zoom-анимации, накладывать HUD
4. `_cut_audio`: вырезать паузы + вставить тишину для zoom
5. `_collect_speech_files`: собрать (mp3, video_t) для наложения
6. `_mux_final`: ffmpeg filter_complex — видео + аудио камеры (с volume) + речь (с adelay)
7. `_concat_with_slides`: сгенерировать слайды, склеить через ffmpeg concat + libx264

## Визуализация тайминга

```
python -m scripts.postprocess.visualize_timeline <session_dir> [--beep-t SEC]
```

Строит `timeline.html` — вертикальная SVG-диаграмма: оригинальный и cut-таймлайн рядом, паузы, zoom-анимации, речь, инструменты.
