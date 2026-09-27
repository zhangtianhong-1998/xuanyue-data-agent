"""把产品文字、用户图片与工具消息转换为 OpenAI 兼容 Chat Completions 请求。"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING

from xuanyue.interfaces import ModelClient
from xuanyue.types import (
    Hint,
    Image,
    Message,
    ModelReply,
    ModelRequest,
    ModelStreamChunk,
    Text,
    ToolCall,
    ToolResult,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletion


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
            if not message.parts:
                raise UnsupportedChatContent("system/user message is empty")
            if message.role == "system" or all(
                isinstance(part, Text) for part in message.parts
            ):
                if any(not isinstance(part, Text) for part in message.parts):
                    raise UnsupportedChatContent(
                        "system message must contain only text"
                    )
                content: str | list[dict[str, object]] = "".join(
                    part.value for part in message.parts
                )
            else:
                # Chat Completions 接收 data URL；原始图片只在模型请求内存中存在。
                blocks: list[dict[str, object]] = []
                for part in message.parts:
                    if isinstance(part, Text):
                        blocks.append({"type": "text", "text": part.value})
                    elif isinstance(part, Image):
                        blocks.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:"
                                    + part.media_type
                                    + ";base64,"
                                    + base64.b64encode(part.data).decode("ascii")
                                },
                            }
                        )
                    else:
                        raise UnsupportedChatContent(
                            "user message contains an unsupported part"
                        )
                content = blocks
            converted.append({"role": message.role, "content": content})
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

    当前只处理文字、用户 PNG/JPEG 图片和函数工具。供应商异常原样抛给调用方；调用方负责
    保存安全的失败类别，不能把已输出的文字片段当成完整答复。
    """

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    @staticmethod
    def _request_kwargs(request: ModelRequest) -> dict[str, object]:
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
        return kwargs

    async def complete(self, request: ModelRequest) -> ModelReply:
        kwargs = self._request_kwargs(request)
        response = await self._client.chat.completions.create(**kwargs)
        return _chat_reply(response, request.tool_mode)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        """逐块转发可见文字；只有收到合法结束标记才交付最终回复。"""
        chunks = await self._client.chat.completions.create(
            **self._request_kwargs(request), stream=True
        )
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        async for item in chunks:
            if not item.choices:
                # 某些服务会单独发送用量信息；它不是回复结束的证明。
                continue
            if len(item.choices) != 1 or item.choices[0].index != 0:
                raise UnsupportedChatContent(
                    "provider returned multiple stream choices"
                )
            choice = item.choices[0]
            delta = choice.delta
            extras = getattr(delta, "model_extra", None) or {}
            if isinstance(extras, Mapping) and "encrypted_content" in extras:
                raise UnsupportedChatContent(
                    "encrypted conversation content is unsupported"
                )
            if (
                getattr(delta, "refusal", None)
                or getattr(delta, "function_call", None)
                or getattr(delta, "audio", None)
            ):
                raise UnsupportedChatContent(
                    "provider returned unsupported stream content"
                )
            if delta.role not in (None, "assistant"):
                raise UnsupportedChatContent("provider changed the stream role")
            if finish_reason is not None and (
                delta.content or delta.tool_calls or choice.finish_reason
            ):
                raise UnsupportedChatContent("provider continued after stream finish")
            if delta.content is not None:
                if not isinstance(delta.content, str):
                    raise UnsupportedChatContent("provider returned non-text content")
                if delta.content:
                    text_parts.append(delta.content)
                    yield ModelStreamChunk(text_delta=delta.content)
            for call in delta.tool_calls or []:
                if (
                    request.tool_mode == "none"
                    or not isinstance(call.index, int)
                    or call.index < 0
                ):
                    raise UnsupportedChatContent(
                        "provider returned an unsupported tool call"
                    )
                current = calls.setdefault(
                    call.index, {"id": "", "name": "", "arguments": ""}
                )
                if call.type not in (None, "function"):
                    raise UnsupportedChatContent(
                        "provider returned a non-function tool call"
                    )
                if call.id:
                    current["id"] += call.id
                if call.function is not None:
                    if call.function.name:
                        current["name"] += call.function.name
                    if call.function.arguments:
                        current["arguments"] += call.function.arguments
            if choice.finish_reason is not None:
                if choice.finish_reason not in ("stop", "tool_calls"):
                    raise UnsupportedChatContent(
                        f"provider stopped: {choice.finish_reason}"
                    )
                finish_reason = choice.finish_reason
        if finish_reason is None:
            raise UnsupportedChatContent("provider stream ended without finish reason")
        if (finish_reason == "tool_calls") != bool(calls):
            raise UnsupportedChatContent("finish reason does not match tool calls")
        parts: list[Text | ToolCall] = []
        if text_parts:
            parts.append(Text("".join(text_parts)))
        for index in sorted(calls):
            call = calls[index]
            if not call["id"] or not call["name"]:
                raise UnsupportedChatContent(
                    "provider returned an incomplete tool call"
                )
            parts.append(ToolCall(call["id"], call["name"], call["arguments"]))
        if not parts:
            raise UnsupportedChatContent("provider returned no text or tool calls")
        yield ModelStreamChunk(reply=ModelReply(tuple(parts)))
