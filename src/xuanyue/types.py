"""Data exchanged across product, model, tool and Agent-kernel boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class Image:
    """仅在运行内存中携带已校验图片；持久化层只保存附件 ID。"""

    media_type: Literal["image/png", "image/jpeg"]
    data: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.media_type not in ("image/png", "image/jpeg"):
            raise ValueError("unsupported image media type")
        if type(self.data) is not bytes or not 0 < len(self.data) <= 5 * 1024 * 1024:
            raise ValueError("image data must be non-empty and at most 5 MiB")


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


Part = Text | Image | ToolCall | ToolResult | Hint
MAX_IMAGES_PER_TURN = 4


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant"]
    parts: tuple[Part, ...]
    private_content_omitted: bool = False


@dataclass(frozen=True, slots=True)
class Task:
    """一次主引擎运行；图片在内存中传递，历史只含已完成的公开问答。"""

    run_id: str
    kernel: str
    model: str
    text: str
    history: tuple[Message, ...] = ()
    images: tuple[Image, ...] = ()

    @property
    def user_parts(self) -> tuple[Text | Image, ...]:
        """先放用户文字，再按选择顺序放图片；两套内核使用相同内容。"""
        return (Text(self.text), *self.images)

    def __post_init__(self) -> None:
        for name in ("run_id", "kernel", "model", "text"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if (
            not isinstance(self.images, tuple)
            or len(self.images) > MAX_IMAGES_PER_TURN
            or any(not isinstance(image, Image) for image in self.images)
        ):
            raise ValueError("this task supports at most four images")
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
            ):
                raise ValueError("history must contain completed public turns")
            if expected_role == "assistant":
                if any(
                    not isinstance(part, Text)
                    or not isinstance(part.value, str)
                    or not part.value.strip()
                    for part in message.parts
                ):
                    raise ValueError("assistant history must contain only text")
            elif (
                not isinstance(message.parts[0], Text)
                or not isinstance(message.parts[0].value, str)
                or not message.parts[0].value.strip()
                or any(not isinstance(part, (Text, Image)) for part in message.parts)
                or any(
                    isinstance(part, Text)
                    and (not isinstance(part.value, str) or not part.value.strip())
                    for part in message.parts
                )
                or sum(isinstance(part, Image) for part in message.parts)
                > MAX_IMAGES_PER_TURN
            ):
                raise ValueError(
                    "user history must start with text and contain at most four images"
                )


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


@dataclass(frozen=True, slots=True)
class ReasoningOption:
    """一项用户可选的推理设置；参数保持供应商语义，由模型客户端转换。"""

    id: str
    label: str
    effort: str | None = None
    thinking: str | None = None
