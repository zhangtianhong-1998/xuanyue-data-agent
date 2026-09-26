"""Data exchanged across product, model, tool and Agent-kernel boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Task:
    run_id: str
    kernel: str
    model: str
    text: str

    def __post_init__(self) -> None:
        for field in ("run_id", "kernel", "model", "text"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Event:
    run_id: str
    seq: int
    kind: str
    payload: Mapping[str, object]


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
