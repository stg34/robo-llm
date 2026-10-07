"""
OpenAI-совместимый бэкенд: Grok, DeepSeek, Gemini и любые провайдеры
с OpenAI Chat Completions API.

История хранится во внутреннем формате (Anthropic-подобные plain dicts),
этот модуль конвертирует её в OpenAI-формат перед каждым вызовом.
"""
import json

import openai

from .base import LLMBackend, NormalizedResponse, ToolCall


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Конвертирует внутренний формат (Anthropic-подобный) в OpenAI Chat формат.

    Ключевые отличия:
    - tool_result → отдельное сообщение role="tool"
    - изображения в tool_result → image_url в следующем user-сообщении
    - image source → image_url с data-URI
    - assistant tool_use → tool_calls
    """
    result = []

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            if isinstance(content, str):
                result.append({"role": "user", "content": content})
                continue

            tool_results = [b for b in content if b.get("type") == "tool_result"]
            observations = [b for b in content if b.get("type") != "tool_result"]

            for tr in tool_results:
                tr_content = tr["content"]
                if isinstance(tr_content, list):
                    # Текст → tool-сообщение, изображения → добавим к наблюдению
                    text_parts = [b["text"] for b in tr_content if b.get("type") == "text"]
                    extra_images = [b for b in tr_content if b.get("type") == "image"]
                    observations = extra_images + observations
                    result.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_use_id"],
                        "content": "\n".join(text_parts),
                    })
                else:
                    text = tr_content if isinstance(tr_content, str) else json.dumps(tr_content)
                    result.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_use_id"],
                        "content": text,
                    })

            if observations:
                oai_content = []
                for block in observations:
                    if block["type"] == "image":
                        src = block["source"]
                        mime = src.get("media_type", "image/jpeg")
                        oai_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{src['data']}"},
                        })
                    elif block["type"] == "text":
                        oai_content.append({"type": "text", "text": block["text"]})
                result.append({"role": "user", "content": oai_content})

        elif role == "assistant":
            if isinstance(content, str):
                result.append({"role": "assistant", "content": content})
                continue

            text = " ".join(
                b["text"] for b in content
                if b.get("type") == "text" and b.get("text")
            )
            tool_uses = [b for b in content if b.get("type") == "tool_use"]

            msg_out: dict = {"role": "assistant", "content": text or None}
            if tool_uses:
                msg_out["tool_calls"] = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b["input"], ensure_ascii=False),
                        },
                    }
                    for b in tool_uses
                ]
            result.append(msg_out)

    return result


# Без явного таймаута SDK ждёт 600 с и делает две повторные попытки — до
# получаса на один залипший запрос. Робот всё это время едет, поэтому режем.
REQUEST_TIMEOUT = 120.0   # секунд на запрос
MAX_RETRIES = 1


class OpenAICompatBackend(LLMBackend):
    def __init__(self, api_key: str, model: str, base_url: str | None = None,
                 temperature: float | None = None):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url,
                                     timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES)
        self.model = model
        # gpt-5.x и o-серия принимают только температуру по умолчанию: на любое
        # другое значение отвечают 400. Гасим здесь, чтобы параметр не ушёл вовсе
        # и чтобы self.temperature честно показывал, что реально отправляется.
        if temperature is not None and model.lower().startswith(("o1", "o3", "o4", "gpt-5")):
            temperature = None
        self.temperature = temperature

    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
    ) -> NormalizedResponse:
        oai_messages = [{"role": "system", "content": system}] + _to_openai_messages(messages)
        oai_tools = _to_openai_tools(tools)

        kwargs = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if oai_tools:
            kwargs["tool_choice"] = "required"
        # gpt-5.x and o-series models require max_completion_tokens instead of max_tokens
        _m = self.model.lower()
        if _m.startswith(("o1", "o3", "o4", "gpt-5")):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        response = self._client.chat.completions.create(
            model=self.model,
            messages=oai_messages,
            tools=oai_tools,
            **kwargs,
        )

        msg = response.choices[0].message

        text_blocks = [msg.content] if msg.content else []
        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                input=json.loads(tc.function.arguments),
            )
            for tc in (msg.tool_calls or [])
        ]

        stop_reason = "tool_use" if tool_calls else "end_turn"

        # Нормализованный content: одинаковый формат для лога и истории
        content_dicts = []
        if msg.content:
            content_dicts.append({"type": "text", "text": msg.content})
        for tc in (msg.tool_calls or []):
            content_dicts.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": json.loads(tc.function.arguments),
            })

        u = response.usage
        usage = {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens}

        return NormalizedResponse(
            stop_reason=stop_reason,
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            content_for_log=content_dicts,
            assistant_message={"role": "assistant", "content": content_dicts},
            usage=usage,
        )
