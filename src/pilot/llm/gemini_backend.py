import base64
import uuid

from google import genai
from google.genai import types

from .base import LLMBackend, NormalizedResponse, ToolCall


def _to_gemini_tools(tools: list[dict]) -> list[types.Tool] | None:
    if not tools:
        return None
    declarations = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters=t.get("input_schema"),
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=declarations)]


def _block_to_part(block: dict) -> types.Part | None:
    btype = block.get("type")
    if btype == "text" and block.get("text"):
        return types.Part.from_text(text=block["text"])
    if btype == "image":
        src = block["source"]
        return types.Part.from_bytes(
            data=base64.b64decode(src["data"]),
            mime_type=src.get("media_type", "image/jpeg"),
        )
    return None


def _to_gemini_contents(messages: list[dict]) -> list[types.Content]:
    # id → имя инструмента — нужно для function_response
    id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg["content"]
            if isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use":
                        id_to_name[b["id"]] = b["name"]

    result: list[types.Content] = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            gemini_role = "user" if role == "user" else "model"
            result.append(types.Content(role=gemini_role, parts=[types.Part.from_text(text=content)]))
            continue

        if role == "user":
            tool_results = [b for b in content if b.get("type") == "tool_result"]
            observations = [b for b in content if b.get("type") != "tool_result"]

            for tr in tool_results:
                tr_content = tr["content"]
                if isinstance(tr_content, list):
                    text = "\n".join(b["text"] for b in tr_content if b.get("type") == "text")
                elif isinstance(tr_content, str):
                    text = tr_content
                else:
                    text = str(tr_content)
                fn_name = id_to_name.get(tr["tool_use_id"], "tool")
                result.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=fn_name, response={"result": text})],
                ))

            if observations:
                parts = [p for b in observations if (p := _block_to_part(b)) is not None]
                if parts:
                    result.append(types.Content(role="user", parts=parts))

        elif role == "assistant":
            parts: list[types.Part] = []
            for block in content:
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    parts.append(types.Part.from_text(text=block["text"]))
                elif btype == "tool_use":
                    parts.append(types.Part.from_function_call(
                        name=block["name"],
                        args=block["input"],
                    ))
            if parts:
                result.append(types.Content(role="model", parts=parts))

    return result


# См. комментарий в openai_compat.py. У google-genai таймаут задаётся
# в миллисекундах через http_options.
REQUEST_TIMEOUT_MS = 120_000


class GeminiBackend(LLMBackend):
    def __init__(self, api_key: str, model: str, temperature: float | None = None, **_):
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
        self.model = model
        self.temperature = temperature

    def _generate(
        self,
        contents: list[types.Content],
        system: str,
        tools: list[types.Tool] | None,
        max_tokens: int,
    ) -> types.GenerateContentResponse:
        config_kwargs: dict = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
        }
        if self.temperature is not None:
            config_kwargs["temperature"] = self.temperature
        if tools:
            config_kwargs["tools"] = tools
            config_kwargs["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY"),
            )
        return self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
    ) -> NormalizedResponse:
        gemini_tools = _to_gemini_tools(tools)
        contents = _to_gemini_contents(messages)

        text_blocks: list[str] = []
        tool_calls: list[ToolCall] = []
        content_dicts: list[dict] = []
        total_in = total_out = 0

        if gemini_tools:
            # Проход 1: только текст — описание сцены.
            # Берём полный system (характер, контекст), но запрещаем вызов инструментов —
            # иначе модель пытается вывести JSON вместо чистого текста.
            # max_output_tokens покрывает внутренние мысли модели (~1000+ токенов).
            describe_system = system + (
                "\n\nСейчас только опиши одним-двумя предложениями что видишь на изображении. "
                "Не вызывай никаких инструментов."
            )
            r1 = self._generate(contents, describe_system, None, max(max_tokens, 4096))
            u1 = r1.usage_metadata
            total_in += u1.prompt_token_count or 0
            total_out += u1.candidates_token_count or 0
            desc_parts = r1.candidates[0].content.parts if r1.candidates[0].content else []
            desc_text = " ".join(p.text for p in desc_parts if p.text).strip()

            # Проход 2: с tools, описание уже в истории → модель выбирает action
            contents2 = contents + [
                types.Content(role="model", parts=[types.Part.from_text(text=desc_text)]),
            ] if desc_text else contents
            r2 = self._generate(contents2, system, gemini_tools, max_tokens)
            u2 = r2.usage_metadata
            total_in += u2.prompt_token_count or 0
            total_out += u2.candidates_token_count or 0
            action_parts = r2.candidates[0].content.parts if r2.candidates[0].content else []

            if desc_text:
                text_blocks.append(desc_text)
                content_dicts.append({"type": "text", "text": desc_text})
            for part in action_parts:
                if part.function_call:
                    fc = part.function_call
                    tc_id = f"call_{uuid.uuid4().hex[:12]}"
                    args = dict(fc.args) if fc.args else {}
                    tool_calls.append(ToolCall(id=tc_id, name=fc.name, input=args))
                    content_dicts.append({"type": "tool_use", "id": tc_id, "name": fc.name, "input": args})
        else:
            # Без tools — один проход
            response = self._generate(contents, system, None, max_tokens)
            u = response.usage_metadata
            total_in = u.prompt_token_count or 0
            total_out = u.candidates_token_count or 0
            for part in (response.candidates[0].content.parts if response.candidates[0].content else []):
                if part.text:
                    text_blocks.append(part.text)
                    content_dicts.append({"type": "text", "text": part.text})

        stop_reason = "tool_use" if tool_calls else "end_turn"
        usage = {"input_tokens": total_in, "output_tokens": total_out}

        return NormalizedResponse(
            stop_reason=stop_reason,
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            content_for_log=content_dicts,
            assistant_message={"role": "assistant", "content": content_dicts},
            usage=usage,
        )
