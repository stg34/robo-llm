"""
ROBOT_TOOLS — JSON-схемы инструментов для Claude Messages API.
TOOL_STATES  — мета для постобработки: состояние робота, терминальность, ключи мета.

Единый источник информации о тулах. Добавить новый инструмент = добавить сюда.

TOOL_STATES поля:
  robot_state  — состояние актора "robot" на tool_start (None если не robot-актор)
  terminal     — True если tool_end не ожидается (done, die)
  meta_keys    — поля из input{} которые попадают в мета интервала
  actor        — актор которому принадлежит инструмент (по умолчанию "robot")
"""

TOOL_STATES: dict[str, dict] = {
    "move":          {"robot_state": "moving",   "terminal": False, "meta_keys": ["meters"]},
    "turn_relative": {"robot_state": "turning",  "terminal": False, "meta_keys": ["degrees"]},
    "turn_absolute": {"robot_state": "turning",  "terminal": False, "meta_keys": ["degrees"]},
    "stop":          {"robot_state": "stopping", "terminal": False},
    "sleep":         {"robot_state": "sleeping", "terminal": False, "meta_keys": ["seconds"]},
    "shoot":         {"robot_state": "shooting", "terminal": False},
    "zoom":          {"robot_state": "zooming",  "terminal": False, "meta_keys": ["cx", "cy"]},
    "done":          {"robot_state": "done",     "terminal": True,  "meta_keys": ["summary"]},
    "die":           {"robot_state": "dying",    "terminal": True},
    "ask_human":     {"robot_state": None,       "terminal": False, "meta_keys": ["question"],
                      "actor": "operator"},
    "set_leds":      {"robot_state": None,       "terminal": False, "meta_keys": ["red", "blue"]},
}


ROBOT_TOOLS = [
    {
        "name": "move",
        "description": (
            "Проехать вперёд или назад по лазерному дальномеру. "
            "Робот едет пока не достигнет целевого расстояния до препятствия впереди. "
            "Рекомендуемый шаг: 0.2–2м. Не вызывай если дальномер показывает < 300мм.\n"
            "Возвращает статус и фактически пройденное расстояние:\n"
            "- ok — выполнено успешно\n"
            "- stuck — дальномер не видел изменений ~1.5с; скорее всего сбой лазера (луч соскользнул), "
            "а не физическое застревание; повернись и продолжи задачу\n"
            "- rangefinder_jump — луч резко прыгнул вверх (соскользнул с препятствия); можно продолжать\n"
            "- obstacle — препятствие < 200мм, двигаться вперёд нельзя; поверни и объедь\n"
            "- timeout — команда не выполнена за отведённое время; оцени ситуацию по камере"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meters": {
                    "type": "number",
                    "description": (
                        "Расстояние в метрах. Положительное = вперёд, отрицательное = назад. "
                        "Рекомендуемый диапазон: ±0.2 … ±2.0."
                    ),
                }
            },
            "required": ["meters"],
        },
    },
    {
        "name": "turn_relative",
        "description": (
            "Повернуть на N градусов от текущего направления. "
            "Использует гироскоп — точность ~1-3°. "
            "Положительные градусы = вправо (по часовой), отрицательные = влево."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "degrees": {
                    "type": "number",
                    "description": (
                        "Угол поворота в градусах. "
                        "Положительный = вправо, отрицательный = влево. "
                        "Диапазон: -180 … +180."
                    ),
                }
            },
            "required": ["degrees"],
        },
    },
    {
        "name": "stop",
        "description": "Немедленно остановить все моторы. Используй при опасной ситуации, затем вызови done().",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "sleep",
        "description": "Подождать N секунд ничего не делая, затем продолжить.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "Количество секунд ожидания. Диапазон: 1 … 60.",
                }
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "die",
        "description": (
            "Немедленно завершить работу всех подсистем и выключить робота. "
            "Вызывай когда задача полностью невыполнима и продолжение бессмысленно."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "zoom",
        "description": (
            "Получить увеличенный фрагмент текущего кадра с центром в точке (cx, cy). "
            "Координаты в пикселях — используй сетку на кадре (вертикальные подписи снизу, "
            "горизонтальные подписи слева). Зум фиксированный 2x: показывает четверть "
            "площади оригинального кадра с максимальной детализацией камеры. "
            "Если центр у края — окно автоматически сдвигается внутрь кадра."
            "Фото передается с сильным сжатием, поэтому мелкие детали могут быть смазаны."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cx": {
                    "type": "integer",
                    "description": "X-координата центра в пикселях (0…896).",
                },
                "cy": {
                    "type": "integer",
                    "description": "Y-координата центра в пикселях (0…504).",
                },
            },
            "required": ["cx", "cy"],
        },
    },
    {
        "name": "ask_human",
        "description": (
            "Обратиться к человеку с вопросом или просьбой и дождаться его ответа. "
            "Робот остановится, озвучит вопрос и будет ждать ввода с клавиатуры. "
            "Не дублируй вопрос в текстовом блоке — он будет озвучен автоматически."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Вопрос или просьба к человеку.",
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "shoot",
        "description": (
            "Нажать на курок. "
            "Сервопривод нажимает на курок и возвращается в исходное положение (~1.2с)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "set_leds",
        "description": "Включить или выключить светодиоды подсветки робота (красный и синий).",
        "input_schema": {
            "type": "object",
            "properties": {
                "red": {
                    "type": "boolean",
                    "description": "true — красный LED включён, false — выключен.",
                },
                "blue": {
                    "type": "boolean",
                    "description": "true — синий LED включён, false — выключен.",
                },
            },
            "required": ["red", "blue"],
        },
    },
    {
        "name": "done",
        "description": "Завершить сессию. Единственный способ закончить работу — других механизмов завершения нет.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Короткая фраза для озвучивания.",
                }
            },
            "required": ["summary"],
        },
    },
]
