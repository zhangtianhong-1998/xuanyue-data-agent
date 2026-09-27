"""Data exchanged across product, model, tool and Agent-kernel boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


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
class Task:
    """一次主引擎运行；history 只含本次之前已完成的文字问答。"""

    run_id: str
    kernel: str
    model: str
    text: str
    history: tuple[Message, ...] = ()

    def __post_init__(self) -> None:
        for field in ("run_id", "kernel", "model", "text"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        # 两种内核都重建根 Agent；完整的公开文字轮次由产品传入，不能夹带
        # 未完成工具调用或被省略的私有内容，避免跨框架重放成另一种含义。
        if not isinstance(self.history, tuple) or len(self.history) % 2:
            raise ValueError("history must contain complete user/assistant pairs")
        for index, message in enumerate(self.history):
            expected_role = "user" if index % 2 == 0 else "assistant"
            if (
                not isinstance(message, Message)
                or message.role != expected_role
                or message.private_content_omitted
                or not isinstance(message.parts, tuple)
                or not message.parts
                or any(
                    not isinstance(part, Text)
                    or not isinstance(part.value, str)
                    or not part.value.strip()
                    for part in message.parts
                )
            ):
                raise ValueError("history must contain completed text-only turns")


@dataclass(frozen=True, slots=True)
class Event:
    run_id: str
    seq: int
    kind: str
    payload: Mapping[str, object]


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


@dataclass(frozen=True, slots=True)
class ModelStreamChunk:
    """一个可见文字增量，或经完整校验的最终模型回复。"""

    text_delta: str | None = None
    reply: ModelReply | None = None
