"""验证推理配置的实际出站 JSON 和私有工具续接边界，不访问真实供应商。"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

import httpx
from openai import AsyncOpenAI

from xuanyue.llm.inference import request_inference_kwargs
from xuanyue.llm.openai_compatible import ChatCompletionsClient, UnsupportedChatContent
from xuanyue.types import (
    Message,
    ModelReply,
    ModelRequest,
    ReasoningOption,
    Text,
    ToolSpec,
)

_PRIVATE_MARKER = "synthetic-private-reasoning-never-publish"
_REQUEST = ModelRequest(
    "configured-model",
    (Message("user", (Text("计算 2 × 3"),)),),
    (ToolSpec("multiply", "Multiply numbers", {"type": "object"}),),
)
_TOOL = {
    "id": "call-1",
    "type": "function",
    "function": {"name": "multiply", "arguments": '{"a":2,"b":3}'},
}


def _reply(
    message: dict[str, object], finish_reason: str = "stop"
) -> dict[str, object]:
    return {
        "id": "synthetic-completion",
        "created": 0,
        "object": "chat.completion",
        "model": "configured-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


def _sse(parts: list[tuple[dict[str, object], str | None]]) -> bytes:
    """用真正的 SDK 解码 SSE，覆盖 extra 字段在协议解析后的表现。"""
    messages = []
    for delta, finish in parts:
        value = {
            "id": "synthetic-completion",
            "created": 0,
            "object": "chat.completion.chunk",
            "model": "configured-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        messages.append("data: " + json.dumps(value) + "\n\n")
    return ("".join(messages) + "data: [DONE]\n\n").encode()


def _sdk(response: dict[str, object] | bytes, requests: list[dict]) -> AsyncOpenAI:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if isinstance(response, bytes):
            return httpx.Response(
                200, content=response, headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json=response)

    return AsyncOpenAI(
        api_key="synthetic-test-key",
        base_url="https://synthetic.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )


class InferenceOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_options_reach_stream_and_complete_http_requests(self) -> None:
        options = (
            (None, {}),
            (ReasoningOption("default", "供应商默认"), {}),
            (
                ReasoningOption("off", "关闭", effort="none"),
                {"reasoning_effort": "none"},
            ),
            (
                ReasoningOption("high", "高", effort="high"),
                {"reasoning_effort": "high"},
            ),
            (
                ReasoningOption("thinking-off", "关闭", thinking="disabled"),
                {"thinking": {"type": "disabled"}},
            ),
            (
                ReasoningOption("auto", "自动", thinking="auto"),
                {"thinking": {"type": "auto"}},
            ),
            (
                ReasoningOption("think-high", "思考 · 高", "high", "enabled"),
                {"reasoning_effort": "high", "thinking": {"type": "enabled"}},
            ),
        )
        for option, expected in options:
            for streaming in (False, True):
                with self.subTest(option=option, streaming=streaming):
                    response = (
                        _sse([({"role": "assistant", "content": "6"}, "stop")])
                        if streaming
                        else _reply({"role": "assistant", "content": "6"})
                    )
                    requests: list[dict] = []
                    async with _sdk(response, requests) as sdk:
                        client = ChatCompletionsClient(sdk, option)
                        if streaming:
                            chunks = [chunk async for chunk in client.stream(_REQUEST)]
                            self.assertEqual(chunks[-1].reply, ModelReply((Text("6"),)))
                        else:
                            self.assertEqual(
                                await client.complete(_REQUEST),
                                ModelReply((Text("6"),)),
                            )
                    sent = requests[0]
                    self.assertEqual(sent["model"], "configured-model")
                    self.assertEqual(sent["tools"][0]["function"]["name"], "multiply")
                    # extra_body 是 SDK 参数，线路上应合并为顶层 thinking 字段。
                    self.assertNotIn("extra_body", sent)
                    self.assertEqual(
                        {
                            key: sent[key]
                            for key in ("reasoning_effort", "thinking")
                            if key in sent
                        },
                        expected,
                    )

    def test_native_effort_values_are_not_remapped(self) -> None:
        for effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                self.assertEqual(
                    request_inference_kwargs(ReasoningOption(effort, effort, effort)),
                    {"reasoning_effort": effort},
                )

    def test_invalid_or_conflicting_options_fail_before_calling_sdk(self) -> None:
        invalid = (
            {"effort": "high"},
            ReasoningOption("", "档位"),
            ReasoningOption("valid", " "),
            ReasoningOption("invalid", "非法", effort="ultra"),
            ReasoningOption("invalid", "非法", effort={"custom": True}),
            ReasoningOption("invalid", "非法", thinking="adaptive"),
            ReasoningOption("invalid", "非法", thinking=["enabled"]),
            ReasoningOption("conflict", "矛盾", "high", "disabled"),
            ReasoningOption("conflict", "矛盾", "none", "enabled"),
        )
        for option in invalid:
            with (
                self.subTest(option=option),
                self.assertRaises((TypeError, ValueError)),
            ):
                # 构造客户端即失败；传入 None SDK 可证明尚未访问 SDK。
                ChatCompletionsClient(None, option)

    async def test_complete_rejects_private_reasoning_with_tools(self) -> None:
        response = _reply(
            {
                "role": "assistant",
                "content": "执行计算",
                "reasoning_content": _PRIVATE_MARKER,
                "tool_calls": [_TOOL],
            },
            "tool_calls",
        )
        async with _sdk(response, []) as sdk:
            with self.assertRaisesRegex(
                UnsupportedChatContent, "private reasoning"
            ) as caught:
                await ChatCompletionsClient(sdk).complete(_REQUEST)
        self.assertNotIn(_PRIVATE_MARKER, str(caught.exception))

    async def test_stream_rejects_private_reasoning_tools_in_either_order(self) -> None:
        reasoning = ({"reasoning_content": _PRIVATE_MARKER}, None)
        tool = ({"tool_calls": [{"index": 0, **_TOOL}]}, None)
        for parts in (
            [reasoning, tool],
            [tool, reasoning],
            [({**reasoning[0], **tool[0]}, None)],
        ):
            with self.subTest(parts_order=[list(part[0]) for part in parts]):
                chunks = []
                async with _sdk(_sse([*parts, ({}, "tool_calls")]), []) as sdk:
                    with self.assertRaisesRegex(
                        UnsupportedChatContent, "private reasoning"
                    ) as caught:
                        async for chunk in ChatCompletionsClient(sdk).stream(_REQUEST):
                            chunks.append(chunk)
                self.assertTrue(all(chunk.reply is None for chunk in chunks))
                self.assertNotIn(_PRIVATE_MARKER, str(caught.exception) + repr(chunks))

    async def test_private_reasoning_without_tools_is_not_returned(self) -> None:
        request = replace(_REQUEST, tools=())
        response = _reply(
            {"role": "assistant", "content": "6", "reasoning_content": _PRIVATE_MARKER}
        )
        async with _sdk(response, []) as sdk:
            self.assertEqual(
                await ChatCompletionsClient(sdk).complete(request),
                ModelReply((Text("6"),)),
            )
        parts = [
            ({"reasoning_content": _PRIVATE_MARKER}, None),
            ({"content": "6"}, "stop"),
        ]
        async with _sdk(_sse(parts), []) as sdk:
            chunks = [
                chunk async for chunk in ChatCompletionsClient(sdk).stream(request)
            ]
        self.assertEqual(chunks[-1].reply, ModelReply((Text("6"),)))
        self.assertEqual("".join(chunk.text_delta or "" for chunk in chunks), "6")
        self.assertNotIn(_PRIVATE_MARKER, repr(chunks))

    async def test_tool_enabled_text_reply_cannot_silently_drop_continuation(
        self,
    ) -> None:
        """工具模式的下一轮也可能需要私有字段，不能只检查本轮有没有工具调用。"""
        message = {
            "role": "assistant",
            "content": "6",
            "reasoning_content": _PRIVATE_MARKER,
        }
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                response = _sse([(message, "stop")]) if streaming else _reply(message)
                async with _sdk(response, []) as sdk:
                    client = ChatCompletionsClient(sdk)
                    with self.assertRaisesRegex(
                        UnsupportedChatContent, "private reasoning"
                    ):
                        if streaming:
                            _ = [chunk async for chunk in client.stream(_REQUEST)]
                        else:
                            await client.complete(_REQUEST)

    async def test_encrypted_content_still_fails_for_both_request_modes(self) -> None:
        message = {"role": "assistant", "content": "6", "encrypted_content": "opaque"}
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                response = _sse([(message, "stop")]) if streaming else _reply(message)
                async with _sdk(response, []) as sdk:
                    client = ChatCompletionsClient(sdk)
                    with self.assertRaisesRegex(UnsupportedChatContent, "encrypted"):
                        if streaming:
                            _ = [chunk async for chunk in client.stream(_REQUEST)]
                        else:
                            await client.complete(_REQUEST)
