import anthropic

from .base import LLMBackend, NormalizedResponse, ToolCall


# См. комментарий в openai_compat.py: дефолтный таймаут SDK слишком велик
# для робота, который в это время продолжает движение.
REQUEST_TIMEOUT = 120.0   # секунд на запрос
MAX_RETRIES = 1


class AnthropicBackend(LLMBackend):
    def __init__(self, api_key: str, model: str, temperature: float | None = None):
        self._client = anthropic.Anthropic(api_key=api_key,
                                           timeout=REQUEST_TIMEOUT,
                                           max_retries=MAX_RETRIES)
        self.model = model
        self.temperature = temperature

    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
    ) -> NormalizedResponse:
        kwargs = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            **kwargs,
        )

        text_blocks = [
            b.text for b in response.content
            if hasattr(b, "text") and b.text.strip()
        ]
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in response.content if b.type == "tool_use"
        ]

        # Сериализуем в plain dicts — одновременно для лога и для истории
        content_dicts = [
            b.model_dump() if hasattr(b, "model_dump") else vars(b)
            for b in response.content
        ]

        u = response.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
        if getattr(u, "cache_creation_input_tokens", None):
            usage["cache_creation_input_tokens"] = u.cache_creation_input_tokens
        if getattr(u, "cache_read_input_tokens", None):
            usage["cache_read_input_tokens"] = u.cache_read_input_tokens

        return NormalizedResponse(
            stop_reason=response.stop_reason,
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            content_for_log=content_dicts,
            assistant_message={"role": "assistant", "content": content_dicts},
            usage=usage,
        )
