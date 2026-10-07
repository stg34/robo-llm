from .base import LLMBackend, NormalizedResponse, ToolCall
from .anthropic_backend import AnthropicBackend
from .gemini_backend import GeminiBackend
from .openai_compat import OpenAICompatBackend


def make_backend(
    model: str,
    api_key: str,
    base_url: str | None = None,
    temperature: float | None = None,
) -> LLMBackend:
    """Фабрика: выбирает бэкенд по имени модели.

    claude-*  → AnthropicBackend
    gemini-*  → GeminiBackend (нативный Google Gemini API)
    всё остальное → OpenAICompatBackend (Grok, DeepSeek, ...)
    """
    if model.startswith("claude"):
        return AnthropicBackend(api_key=api_key, model=model, temperature=temperature)
    if model.startswith("gemini"):
        return GeminiBackend(api_key=api_key, model=model, temperature=temperature)
    return OpenAICompatBackend(api_key=api_key, model=model, base_url=base_url,
                               temperature=temperature)
