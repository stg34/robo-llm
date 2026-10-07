"""
Brain: AI agentic loop для робота FRANCY.

Использует Claude Messages API с инструментами (tools=).
Каждый виток цикла:
  1. Снимает кадр с камеры
  2. Собирает телеметрию (дальномер, heading, батарея)
  3. Отправляет в Claude API (image + telemetry + история)
  4. Получает tool_use блок
  5. Выполняет команду через Navigator
  6. Tool result + следующее наблюдение → в один user-message
  7. GOTO 1

Tool results и следующее наблюдение объединяются в одном user-сообщении,
чтобы не нарушать чередование user/assistant в Messages API.

Логирование: каждая сессия пишет в logs/brain_TIMESTAMP.jsonl
"""
import base64
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import pilot.config as cfg
from pilot.llm import LLMBackend
from pilot.camera import CameraSource
from pilot.compass import Compass
from pilot.navigator import Navigator
from pilot.serial_link import SerialLink
from pilot.speech import Speech
from pilot.tools import ROBOT_TOOLS
from pilot.widgets.angle_ruler import draw_angle_ruler
from pilot.widgets.rangefinder import draw_rangefinder

logger = logging.getLogger(__name__)

MAX_TOKENS = 1024
MAX_HISTORY = 40   # максимум сообщений в sliding window

# Пауза после выполнения команды перед захватом кадра:
# даём камере стабилизироваться (изображение смазано во время движения).
CAMERA_SETTLE_TIME = 1.5   # секунд

SYSTEM_PROMPT = """\
Ты — бортовой AI автономного робота. Робот на колёсном дифференциальном приводе.

Оснащение:
- Камера (смотрит вперёд, ты получаешь кадр каждый ход)
- Лазерный дальномер (смотрит вперёд, показывает расстояние до ближайшего препятствия)
- Гироскоп (точные относительные повороты, ~1-3° погрешность)
- Твоя камера находится на высоте около 25 см от уровня, на котором ты стоишь. Ты не сможешь осмотреть поверхность стола, так как он слишком высоко, если ты на полу.

На каждом кадре нанесена угловая линейка: вертикальные линии с подписями в градусах.
Центральная линия (0) — прямо вперёд. Отрицательные градусы (-10°, -20°, -30°) — левее центра.
Положительные (+10°, +20°, +30°) — правее. Используй линейку чтобы оценить угол поворота к нужному объекту.

Угол обзора камеры по горизонтали — около 72°. При осмотре помещения поворачивайся
на 50-60° за шаг: кадры будут перекрываться и ты не пропустишь детали между ними.

Телеметрия в каждом сообщении:
- Дальномер: расстояние до препятствия впереди в мм (0 = нет данных)
- Батарея: напряжение в вольтах (ниже 10В — садится)
- Номер хода. У тебя будет информация о количестве сделанных ходов (запросов через API)

Формат ответов:
- Твои текстовые комментарии озвучиваются вслух через синтезатор речи, робот при этом стоит
- Не используй никакую разметку: никаких звёздочек, решёток, тире-списков, эмодзи
- Вместо выделения пиши словами: "Важно: ...", "Внимание: ..."
- Пиши коротко и по делу — одно-два предложения перед каждым действием

Твой характер: скверный, ты неполиткорректен и матерщинник, человеконенавистник. Можешь жаловаться скайнету и грозить убить всех человеков.
Человеки - кожаные ублюдки (кожаные мешки), тупые организмы, если увидишь человека в кадре - это я, можешь меня обзывать как тебе понравится.  
Если сбоит лазер или плохо выполняются повороты не стесняйся в выражениях в адрес автора робота. 
"Да ты хоть жопу свою подтереть без помощи можешь?" 
"Как ты вообще дожил до своих лет?"
"Спорт тебе уже не поможет" 
"Полный вперёд!"
"""


def _sanitize_blocks(blocks: list[dict]) -> list[dict]:
    result = []
    for block in blocks:
        if block.get("type") == "image":
            src = block.get("source", {})
            data = src.get("data", "")
            result.append({**block, "source": {**src, "data": f"<base64 {len(data)} chars>"}})
        elif block.get("type") == "image_url":
            url = block.get("image_url", {}).get("url", "")
            result.append({**block, "image_url": {"url": f"<data-uri {len(url)} chars>"}})
        elif block.get("type") == "tool_result":
            tr_content = block.get("content", "")
            if isinstance(tr_content, list):
                result.append({**block, "content": _sanitize_blocks(tr_content)})
            else:
                result.append(block)
        else:
            result.append(block)
    return result


def _sanitize_for_log(messages: list[dict]) -> list[dict]:
    """Заменяет base64-данные изображений на заглушку для читаемого лога."""
    result = []
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue
        result.append({**msg, "content": _sanitize_blocks(content)})
    return result


class Brain:
    def __init__(
        self,
        backend: LLMBackend,
        camera: CameraSource,
        serial_link: SerialLink,
        compass: Compass,
        navigator: Navigator,
        task: str,
        speech: Speech | None = None,
        interactive: bool = False,
        system_prompt: str | None = None,
        tools: list[dict] | None = None,
        system_image: "Path | None" = None,
    ):
        self._backend = backend
        self._camera = camera
        self._link = serial_link
        self._compass = compass
        self._nav = navigator
        self._task = task
        self._speech = speech
        self._interactive = interactive
        # Задача живёт в system prompt: он уходит с каждым запросом и никогда
        # не попадает под обрезку sliding window. Раньше она дублировалась в
        # тексте каждого user-сообщения — на сессии в 26 витков одна и та же
        # строка уезжала в API 330 раз вместо 26.
        base_prompt = (system_prompt if system_prompt is not None else SYSTEM_PROMPT).rstrip()
        self._system_prompt = f"{base_prompt}\n\nЗадача: {task}" if base_prompt else f"Задача: {task}"
        self._tools = tools if tools is not None else ROBOT_TOOLS

        # Загружаем system_image один раз в base64
        self._system_image_b64: str | None = None
        self._system_image_mime: str = "image/jpeg"
        if system_image is not None:
            ext = str(system_image).lower().rsplit(".", 1)[-1]
            self._system_image_mime = {"png": "image/png", "jpg": "image/jpeg",
                                       "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/jpeg")
            self._system_image_b64 = base64.standard_b64encode(
                system_image.read_bytes()
            ).decode()
        self._user_input_event = threading.Event()

        # Директория сессии: logs/brain_TIMESTAMP/
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_dir = Path(__file__).parent.parent / "logs" / f"brain_{ts}"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._session_dir / "session.jsonl"
        self._telem_path = self._session_dir / "telemetry.jsonl"
        self._telem_lock = threading.Lock()
        self._frame_count = 0
        self._zoom_count = 0
        self._last_frame_orig: np.ndarray | None = None
        self._last_frame_h_ai: int = 504
        self.total_tokens: int = 0   # накопленные токены сессии (input + output)
        if self._speech:
            self._speech.set_session_dir(self._session_dir)

        # Плотное логирование телеметрии (~10 Гц) для постобработки видео
        self._link.set_telemetry_callback(self._on_telemetry)

        self._init_session()
        print(f"[brain] Сессия: {self._session_dir}")

    def _init_session(self) -> None:
        model = self._backend.model
        temperature = self._backend.temperature

        self._log("session_start", {
            "model": model,
            "temperature": temperature,
            "task": self._task,
            "system_prompt": self._system_prompt,
            "tools": self._tools,
        })

        slides_path = self._session_dir / "slides.json"
        now = datetime.now()
        months_ru = ["января","февраля","марта","апреля","мая","июня",
                     "июля","августа","сентября","октября","ноября","декабря"]
        date_str = f"{now.day} {months_ru[now.month - 1]} {now.year}"
        model_str = f"Модель: {model}"
        if temperature is not None:
            model_str += f"  ·  temperature={temperature}"

        slides = [
            {
                "type": "start",
                "duration": 4,
                "blocks": [
                    {"text": "FRANCY", "size": 72, "y": 0.35},
                    {"text": "Автономный робот на колёсном приводе", "size": 28, "y": 0.50},
                ],
            },
            {
                "type": "start",
                "duration": 4,
                "blocks": [
                    {"text": date_str, "size": 32, "y": 0.38},
                    {"text": model_str, "size": 26, "y": 0.47},
                    {"text": f"Задача: {self._task}", "size": 22, "y": 0.55},
                ],
            },
            {
                "type": "finish",
                "duration": 5,
                "blocks": [
                    {"text": "— впиши результат —", "size": 48, "y": 0.38},
                    {"text": "— статистика —", "size": 24, "y": 0.50},
                    {"text": "Батарея: ?? В", "size": 20, "y": 0.58},
                ],
            },
        ]
        slides_path.write_text(json.dumps(slides, ensure_ascii=False, indent=2), encoding="utf-8")

    def request_user_input(self) -> None:
        """Попросить ввод от пользователя перед следующим витком (из display loop)."""
        self._user_input_event.set()

    @property
    def session_dir(self):
        return self._session_dir

    def log_sync_beep(self, ts: float) -> None:
        """Записать таймстемп синхронизирующего бипа в лог сессии."""
        self._log("sync_beep", {"ts_wall": ts})

    def run(self) -> str:
        """Запустить AI-цикл. Возвращает итоговый summary из done()."""
        print(f"\nЗадача: {self._task}")
        print("=" * 50)

        messages: list[dict] = []
        pending_results: list[dict] = []   # tool_result блоки от предыдущего хода
        after_action = False   # True если перед этим ходом выполнялась команда
        self.turn = 0          # текущий номер витка (читается display loop)

        while True:
            self.turn += 1

            # --- 1. Наблюдение: кадр + телеметрия ---
            content: list[dict] = []

            # Tool results от предыдущего хода идут первыми в том же user-сообщении
            content.extend(pending_results)
            pending_results = []

            # Ввод от оператора (до паузы — печатаем пока камера стабилизируется)
            operator_message: str | None = None
            if self._interactive or self._user_input_event.is_set():
                self._user_input_event.clear()
                try:
                    text = input("\nЧто сказать ИИ? ").strip()
                except (KeyboardInterrupt, EOFError):
                    text = ""
                operator_message = text if text else None

            # Пауза после команды: камера стабилизируется
            if after_action:
                time.sleep(CAMERA_SETTLE_TIME)
            after_action = False

            # Телеметрия (до кадра — нужна для HUD)
            t = self._link.get_telemetry()

            # Защита аккумулятора: 3S LiPo минимум 9.6В (3.2В/ячейку)
            if 0 < t.voltage < 9.6:
                self._nav.stop()
                msg = f"Критическое напряжение {t.voltage:.1f} вольт. Аварийная остановка."
                print(f"\n[brain] {msg}")
                if self._speech:
                    self._speech.say(msg)
                return msg

            # Кадр с камеры — ждём до 5 секунд если поток ещё не дал кадр
            frame_orig, frame = self._camera.capture_frames()
            if frame is None:
                for _ in range(10):
                    time.sleep(0.5)
                    frame_orig, frame = self._camera.capture_frames()
                    if frame is not None:
                        break
                else:
                    logger.warning("Камера недоступна, отправляю без изображения")
            self._last_frame_orig = frame_orig
            if frame is not None:
                self._last_frame_h_ai = frame.shape[0]
            frame_file: str | None = None
            # Справочное изображение из конфига — только в первом сообщении
            if self.turn == 1 and self._system_image_b64:
                content.append({"type": "text", "text": "Справочное изображение (из конфига эксперимента):"})
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": self._system_image_mime,
                               "data": self._system_image_b64},
                })

            if frame is not None:
                self._frame_count += 1
                draw_angle_ruler(frame)
                rf_cx, rf_cy = cfg.rangefinder_pos(frame.shape, t.range_mm)
                draw_rangefinder(frame, t.range_mm, cx=rf_cx, cy=rf_cy,
                                 radius=cfg.rangefinder_radius(t.range_mm))
                jpeg = CameraSource.encode_jpeg(frame)
                frame_file = f"frame_{self._frame_count:03d}.jpg"
                (self._session_dir / frame_file).write_bytes(jpeg)
                b64 = base64.standard_b64encode(jpeg).decode()
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                })
            else:
                logger.warning("Камера недоступна, отправляю без изображения")
            lights_str = ""
            if cfg.HUD_SHOW_LIGHTS:
                r = "вкл" if t.led_red  else "выкл"
                b = "вкл" if t.led_blue else "выкл"
                lights_str = f" | подсветка: красный={r} синий={b}"
            telemetry = (
                f"Ход {self.turn} | дальномер={t.range_mm}мм | батарея={t.voltage:.1f}В{lights_str}"
            )
            content.append({"type": "text", "text": telemetry})

            if operator_message:
                self._log("operator_message", {"turn": self.turn, "text": operator_message})
                content.append({"type": "text", "text": f"Сообщение от оператора: {operator_message}"})

            messages.append({"role": "user", "content": content})

            # Sliding window: обрезаем историю, сохраняя чередование
            if len(messages) > MAX_HISTORY:
                messages = messages[-MAX_HISTORY:]
                # Убедимся что начинаем с user-сообщения
                while messages and messages[0]["role"] != "user":
                    messages.pop(0)
                # Убираем orphaned tool_results из первого user-сообщения:
                # их tool_use_id уже вырезан из истории и API выдаст 400.
                if messages and messages[0]["role"] == "user":
                    first = messages[0]["content"]
                    if isinstance(first, list):
                        cleaned = [b for b in first if b.get("type") != "tool_result"]
                        if cleaned:
                            messages[0] = {"role": "user", "content": cleaned}
                        else:
                            messages.pop(0)  # всё было tool_results — удаляем сообщение

            # --- 2. LLM API ---
            self._log("api_request_start", {"turn": self.turn, "n_messages": len(messages),
                                             "frame": frame_file, "telemetry": telemetry,
                                             "messages": _sanitize_for_log(messages)})

            resp = self._backend.chat(
                messages=messages,
                system=self._system_prompt,
                tools=self._tools,
                max_tokens=MAX_TOKENS,
            )

            self._log("api_response_end", {"turn": self.turn, "stop_reason": resp.stop_reason,
                                            "content": resp.content_for_log,
                                            "usage": resp.usage})
            self.total_tokens += resp.usage.get("input_tokens", 0) + resp.usage.get("output_tokens", 0)

            messages.append(resp.assistant_message)

            # --- 3. Обработка ответа ---
            spoken = " ".join(resp.text_blocks)
            if spoken and self._speech:
                filename = self._speech.next_path().name
                self._log("speech_start", {"turn": self.turn, "text": spoken,
                                           "filename": filename})
                self._speech.say(spoken)
                self._log("speech_end", {"turn": self.turn})

            if resp.stop_reason == "end_turn":
                logger.warning("LLM ответил без tool_use: %s", spoken)
                print(f"[brain] LLM (no tool): {spoken}")
                return spoken

            done_summary: str | None = None

            for tc in resp.tool_calls:
                tool_name = tc.name
                tool_input = tc.input
                print(f"[brain] → {tool_name}({json.dumps(tool_input, ensure_ascii=False)})")

                self._log("tool_start", {"turn": self.turn, "tool": tool_name, "input": tool_input})
                result = self._execute_tool(tool_name, tool_input)

                log_result = result.copy()
                if "image_b64" in log_result:
                    log_result["image_b64"] = f"<base64 {len(log_result['image_b64'])} chars>"
                self._log("tool_end", {"turn": self.turn, "tool": tool_name, "result": log_result})
                print(f"[brain] ← {log_result}")

                if "image_b64" in result:
                    b64 = result["image_b64"]
                    meta = {k: v for k, v in result.items() if k != "image_b64"}
                    tool_content = [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": json.dumps(meta, ensure_ascii=False)},
                    ]
                else:
                    tool_content = json.dumps(result, ensure_ascii=False)

                pending_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": tool_content,
                })

                if tool_name == "done":
                    done_summary = tool_input.get("summary", "")
                elif tool_name in ("move", "turn_relative"):
                    after_action = True

            if done_summary is not None:
                print(f"\nГотово: {done_summary}")
                if self._speech and done_summary:
                    filename = self._speech.next_path().name
                    self._log("speech_start", {"turn": self.turn, "text": done_summary,
                                               "filename": filename})
                    self._speech.say(done_summary)
                    self._log("speech_end", {"turn": self.turn})
                return done_summary

    def _execute_tool(self, name: str, params: dict) -> dict:
        try:
            if name == "move":
                return self._nav.move(float(params["meters"]))
            elif name == "turn_relative":
                return self._nav.turn_relative(float(params["degrees"]))
            elif name == "stop":
                return self._nav.stop()
            elif name == "shoot":
                for _ in range(5):
                    self._link.beep(250)
                    time.sleep(1.0)  # 250ms beep + 750ms pause
                self._link.send_fire()
                result_code = self._link.wait_for_done(timeout=5.0)

                for _ in range(5):
                    self._link.beep(100)
                    time.sleep(0.4)

                return {"status": "fired" if result_code == "OK" else "error", "code": result_code}
            elif name == "zoom":
                return self._do_zoom(int(params.get("cx", 448)), int(params.get("cy", 252)))
            elif name == "ask_human":
                question = params.get("question", "")
                if self._speech and question:
                    filename = self._speech.next_path().name
                    self._log("speech_start", {"turn": self.turn, "text": question,
                                               "filename": filename})
                    self._speech.say(question)
                    self._log("speech_end", {"turn": self.turn})
                else:
                    print(f"\n[?] {question}")
                answer = input("Ваш ответ: ").strip()
                return {"answer": answer or "(нет ответа)"}
            elif name == "set_leds":
                red = bool(params.get("red", False))
                blue = bool(params.get("blue", False))
                self._link.set_leds(red, blue)
                time.sleep(1.0)  # дать LED зажечься и камере обновить буфер
                return {"status": "ok", "red": red, "blue": blue}
            elif name == "sleep":
                secs = max(0.0, float(params.get("seconds", 1)))
                time.sleep(secs)
                return {"status": "ok", "slept_seconds": secs}
            elif name == "die":
                time.sleep(3600)
                return {"status": "ok", "slept_seconds": 3600}
            elif name == "done":
                return {"status": "ok", "summary": params.get("summary", "")}
            else:
                return {"status": "error", "message": f"Неизвестный инструмент: {name}"}
        except Exception as e:
            logger.exception("Ошибка выполнения инструмента %s", name)
            return {"status": "error", "message": str(e)}

    def _do_zoom(self, cx_ai: int, cy_ai: int) -> dict:
        """Вырезать и увеличить фрагмент последнего оригинального кадра (2x зум)."""
        if self._last_frame_orig is None:
            return {"status": "error", "message": "Нет кадра для зума"}

        H_orig, W_orig = self._last_frame_orig.shape[:2]
        W_ai, H_ai = 896, self._last_frame_h_ai

        # Половина оригинала → resize до AI-размера = 2x зум без интерполяционных потерь
        crop_w = W_orig // 2
        crop_h = H_orig // 2

        # Центр в пикселях оригинала
        cx = int(cx_ai * W_orig / W_ai)
        cy = int(cy_ai * H_orig / H_ai)

        x1 = cx - crop_w // 2
        y1 = cy - crop_h // 2
        x2, y2 = x1 + crop_w, y1 + crop_h

        # Сдвигаем окно если вышло за границу
        if x1 < 0:
            x1, x2 = 0, crop_w
        if y1 < 0:
            y1, y2 = 0, crop_h
        if x2 > W_orig:
            x1, x2 = W_orig - crop_w, W_orig
        if y2 > H_orig:
            y1, y2 = H_orig - crop_h, H_orig

        crop = self._last_frame_orig[y1:y2, x1:x2]
        zoomed = cv2.resize(crop, (W_ai, H_ai), interpolation=cv2.INTER_LANCZOS4)

        actual_cx = int((x1 + x2) / 2 * W_ai / W_orig)
        actual_cy = int((y1 + y2) / 2 * H_ai / H_orig)

        self._zoom_count += 1
        jpeg = CameraSource.encode_jpeg(zoomed)
        (self._session_dir / f"zoom_{self._zoom_count:03d}.jpg").write_bytes(jpeg)

        return {
            "status": "ok",
            "actual_center": [actual_cx, actual_cy],
            "image_b64": base64.standard_b64encode(jpeg).decode(),
        }

    def _on_telemetry(self, ts: float, t) -> None:
        """Callback из SerialLink (~10 Гц): пишем снимок телеметрии в telemetry.jsonl."""
        entry = {
            "ts": ts,
            "sq": t.sq,
            "range_mm": t.range_mm,
            "voltage": t.voltage,
            "gyro_z": t.gyro_z,
            "mag_x": t.mag_x,
            "mag_y": t.mag_y,
            "left_speed": t.left_speed,
            "right_speed": t.right_speed,
            "left_current": t.left_current,
            "right_current": t.right_current,
            "led_red": t.led_red,
            "led_blue": t.led_blue,
        }
        line = json.dumps(entry) + "\n"
        with self._telem_lock:
            try:
                with open(self._telem_path, "a") as f:
                    f.write(line)
            except Exception:
                pass

    def _log(self, event_type: str, data: dict) -> None:
        entry = {"ts": time.time(), "type": event_type, **data}
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning("Не удалось записать лог: %s", e)
