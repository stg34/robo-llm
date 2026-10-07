from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class NormalizedResponse:
    stop_reason: str           # "tool_use" | "end_turn"
    text_blocks: list[str]
    tool_calls: list[ToolCall]
    content_for_log: list[dict]   # в api_response_end["content"]
    assistant_message: dict       # plain dict для messages-истории
    usage: dict                   # {"input_tokens": int, "output_tokens": int, ...}


class LLMBackend(ABC):
    model: str
    temperature: float | None

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        max_tokens: int,
    ) -> NormalizedResponse: ...
