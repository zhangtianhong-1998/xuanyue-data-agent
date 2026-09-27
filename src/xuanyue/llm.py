"""按模型 ID 分派，并把产品文字消息转换为 Chat Completions 请求。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from .interfaces import ModelClient
from .types import Hint, Message, ModelReply, ModelRequest, Text, ToolCall, ToolResult

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletion


class ModelUnavailable(LookupError):
    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f"model {model!r} is not registered")


class ModelRouter(ModelClient):
    """按模型 ID 分派给已登记后端；这里尚不实现供应商 API。"""

    def __init__(self, backends: Mapping[str, ModelClient]) -> None:
        if any(not name for name in backends):
            raise ValueError("model ids must be non-empty")
        self._backends = dict(backends)

    async def complete(self, request: ModelRequest) -> ModelReply:
        try:
            backend = self._backends[request.model]
        except KeyError as exc:
            raise ModelUnavailable(request.model) from exc
        return await backend.complete(request)


class UnsupportedChatContent(ValueError):
    """供应商的 Chat Completions 协议无法安全表示当前消息或回复。"""


def _flush_assistant(
    converted: list[dict[str, object]], text_parts: list[str], calls: list[ToolCall]
) -> None:
    """把一轮 assistant 文字和工具调用写入协议消息。"""
    if not text_parts and not calls:
        return
    item: dict[str, object] = {
        "role": "assistant",
        "content": "".join(text_parts) if text_parts else None,
    }
    if calls:
        item["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ]
    converted.append(item)
    text_parts.clear()
    calls.clear()


def _chat_messages(messages: tuple[Message, ...]) -> list[dict[str, object]]:
    """将 AgentScope 累积在一条消息中的工具历史拆成 assistant/tool 消息。"""
    converted: list[dict[str, object]] = []
    for message in messages:
        if message.private_content_omitted:
            raise UnsupportedChatContent(
                "message omitted private content needed for replay"
            )
        if message.role in ("system", "user"):
            if not message.parts or any(
                not isinstance(part, Text) for part in message.parts
            ):
                raise UnsupportedChatContent(
                    "system/user messages must contain only text"
                )
            converted.append(
                {
                    "role": message.role,
                    "content": "".join(part.value for part in message.parts),
                }
            )
            continue
        if message.role != "assistant":
            raise UnsupportedChatContent(f"unsupported role: {message.role!r}")

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        awaiting_results: dict[str, str] = {}
        seen_ids: set[str] = set()
        before = len(converted)

        for part in message.parts:
            if isinstance(part, Text):
                if calls or awaiting_results:
                    raise UnsupportedChatContent(
                        "text appeared between tool calls and results"
                    )
                text_parts.append(part.value)
            elif isinstance(part, Hint):
                if awaiting_results:
                    raise UnsupportedChatContent(
                        "hint appeared before all tool results"
                    )
                # 与 AgentScope 的 OpenAIChatFormatter 一致：运行时提示另起 user 消息。
                _flush_assistant(converted, text_parts, calls)
                converted.append({"role": "user", "content": part.text})
            elif isinstance(part, ToolCall):
                if (
                    not part.id
                    or not part.name
                    or part.id in seen_ids
                    or (awaiting_results and not calls)
                ):
                    raise UnsupportedChatContent("invalid or interleaved tool call")
                seen_ids.add(part.id)
                awaiting_results[part.id] = part.name
                calls.append(part)
            elif isinstance(part, ToolResult):
                if awaiting_results.get(part.id) != part.name:
                    raise UnsupportedChatContent("tool result does not match its call")
                _flush_assistant(converted, text_parts, calls)
                content = part.output
                if part.state != "success":
                    content = f"[tool status: {part.state}] {content}"
                converted.append(
                    {"role": "tool", "tool_call_id": part.id, "content": content}
                )
                del awaiting_results[part.id]
            else:
                raise UnsupportedChatContent(
                    f"unsupported message part: {type(part).__name__}"
                )
        _flush_assistant(converted, text_parts, calls)
        if awaiting_results:
            raise UnsupportedChatContent("tool calls are missing results")
        if len(converted) == before:
            raise UnsupportedChatContent("assistant message is empty")
    return converted


def _chat_reply(response: ChatCompletion, tool_mode: str) -> ModelReply:
    """只接纳完整的文字或函数调用；截断回复不能当成成功结果。"""
    if not response.choices:
        raise UnsupportedChatContent("provider returned no choices")
    choice = response.choices[0]
    if choice.finish_reason not in ("stop", "tool_calls"):
        raise UnsupportedChatContent(f"provider stopped: {choice.finish_reason}")
    message = choice.message
    # 加密思考字段需要原样回传；当前产品消息模型无法保存，不能悄悄丢弃。
    extras = getattr(message, "model_extra", None) or {}
    if isinstance(extras, Mapping) and "encrypted_content" in extras:
        raise UnsupportedChatContent("encrypted conversation content is unsupported")
    parts: list[Text | ToolCall] = []
    if message.content is not None:
        if not isinstance(message.content, str):
            raise UnsupportedChatContent("provider returned non-text content")
        if message.content:
            parts.append(Text(message.content))
    tool_calls = message.tool_calls or []
    if (choice.finish_reason == "tool_calls") != bool(tool_calls):
        raise UnsupportedChatContent("finish reason does not match tool calls")
    for call in tool_calls:
        if (
            tool_mode == "none"
            or call.type != "function"
            or not call.id
            or not call.function.name
            or not isinstance(call.function.arguments, str)
        ):
            raise UnsupportedChatContent("provider returned an unsupported tool call")
        parts.append(ToolCall(call.id, call.function.name, call.function.arguments))
    if not parts:
        raise UnsupportedChatContent("provider returned no text or tool calls")
    return ModelReply(tuple(parts))


class ChatCompletionsClient(ModelClient):
    """OpenAI 兼容聊天后端；调用方管理 SDK 客户端、密钥和关闭时机。

    当前只处理文字和函数工具。供应商异常原样抛给调用方；Agent 事件层尚未
    形成失败终态，调用方必须单独记录错误，不能把开始事件当成任务完成。
    """

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def complete(self, request: ModelRequest) -> ModelReply:
        kwargs: dict[str, object] = {
            "model": request.model,
            "messages": _chat_messages(request.messages),
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]
            kwargs["tool_choice"] = request.tool_mode
        elif request.tool_mode == "none":
            kwargs["tool_choice"] = "none"
        response = await self._client.chat.completions.create(**kwargs)
        return _chat_reply(response, request.tool_mode)
