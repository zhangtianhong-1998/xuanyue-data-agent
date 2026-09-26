"""Text-and-tool model exchange, independent of the Agent kernel SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON supplied by the model; the tool layer validates it.


@dataclass(frozen=True, slots=True)
class ToolResult:
    id: str
    name: str
    output: str
    state: str


@dataclass(frozen=True, slots=True)
class Hint:
    text: str
    source: str | None


Part = Text | ToolCall | ToolResult | Hint


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant"]
    parts: tuple[Part, ...]
    private_content_omitted: bool = False


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    tool_mode: Literal["auto", "none"] = "auto"


@dataclass(frozen=True, slots=True)
class ModelReply:
    parts: tuple[Text | ToolCall, ...]


class LLM(Protocol):
    async def complete(self, request: ModelRequest) -> ModelReply: ...
