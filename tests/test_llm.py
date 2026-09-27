"""验证产品消息到 Chat Completions 的工具历史转换，不访问供应商。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from xuanyue.llm import ChatCompletionsClient, UnsupportedChatContent
from xuanyue.llm.openai_compatible import _chat_messages
from xuanyue.types import (
    Hint,
    Message,
    ModelReply,
    ModelRequest,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
)


def _response(content=None, tool_calls=(), finish_reason="stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ]
    )


class _FakeCompletions:
    def __init__(self, response) -> None:
        self.response = response
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


class ChatCompletionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_stream_never_becomes_a_final_reply(self) -> None:
        async def fragments():
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        index=0,
                        delta=SimpleNamespace(
                            role="assistant", content="partial", tool_calls=None
                        ),
                        finish_reason=None,
                    )
                ]
            )

        api = _FakeCompletions(fragments())
        client = ChatCompletionsClient(
            SimpleNamespace(chat=SimpleNamespace(completions=api))
        )
        received = []
        with self.assertRaisesRegex(UnsupportedChatContent, "without finish reason"):
            async for chunk in client.stream(
                ModelRequest("model-a", (Message("user", (Text("hi"),)),), ())
            ):
                received.append(chunk)
        self.assertEqual([item.text_delta for item in received], ["partial"])
        self.assertTrue(all(item.reply is None for item in received))
        self.assertTrue(api.requests[0]["stream"])

    async def test_tool_request_and_reply_keep_call_id(self) -> None:
        call = SimpleNamespace(
            type="function",
            id="call-1",
            function=SimpleNamespace(name="multiply", arguments='{"a":2,"b":3}'),
        )
        api = _FakeCompletions(_response(tool_calls=[call], finish_reason="tool_calls"))
        client = ChatCompletionsClient(
            SimpleNamespace(chat=SimpleNamespace(completions=api))
        )
        request = ModelRequest(
            "model-a",
            (Message("user", (Text("Check 2 × 3."),)),),
            (
                ToolSpec(
                    "multiply",
                    "Multiply numbers.",
                    {"type": "object", "properties": {"a": {"type": "integer"}}},
                ),
            ),
        )

        result = await client.complete(request)

        self.assertEqual(
            result, ModelReply((ToolCall("call-1", "multiply", '{"a":2,"b":3}'),))
        )
        self.assertEqual(api.requests[0]["model"], "model-a")
        self.assertEqual(api.requests[0]["tool_choice"], "auto")
        self.assertEqual(api.requests[0]["tools"][0]["function"]["name"], "multiply")

    async def test_accumulated_tool_history_is_split_into_valid_messages(self) -> None:
        # AgentScope 将多次工具调用与结果累积在同一条 assistant 消息中。
        history = Message(
            "assistant",
            (
                Text("First check. "),
                ToolCall("one", "multiply", '{"a":2,"b":3}'),
                ToolResult("one", "multiply", "6", "success"),
                ToolCall("two", "multiply", '{"a":3,"b":4}'),
                ToolResult("two", "multiply", "12", "success"),
                Hint("No more tools.", "agent"),
            ),
        )
        api = _FakeCompletions(_response(content="Done."))
        client = ChatCompletionsClient(
            SimpleNamespace(chat=SimpleNamespace(completions=api))
        )

        result = await client.complete(ModelRequest("model-a", (history,), (), "none"))

        self.assertEqual(result, ModelReply((Text("Done."),)))
        sent = api.requests[0]
        self.assertEqual(sent["tool_choice"], "none")
        self.assertEqual(
            [item["role"] for item in sent["messages"]],
            ["assistant", "tool", "assistant", "tool", "user"],
        )
        self.assertEqual(sent["messages"][0]["tool_calls"][0]["id"], "one")
        self.assertEqual(sent["messages"][1]["tool_call_id"], "one")
        self.assertEqual(sent["messages"][2]["tool_calls"][0]["id"], "two")
        self.assertEqual(sent["messages"][3]["tool_call_id"], "two")
        self.assertEqual(sent["messages"][4]["content"], "No more tools.")

    async def test_incomplete_or_unsupported_messages_fail_explicitly(self) -> None:
        with self.assertRaisesRegex(UnsupportedChatContent, "missing results"):
            _chat_messages((Message("assistant", (ToolCall("one", "x", "{}"),)),))
        with self.assertRaisesRegex(UnsupportedChatContent, "private content"):
            _chat_messages((Message("assistant", (Text("visible"),), True),))
        with self.assertRaisesRegex(UnsupportedChatContent, "does not match"):
            _chat_messages(
                (
                    Message(
                        "assistant",
                        (
                            ToolCall("one", "multiply", "{}"),
                            ToolResult("one", "different_tool", "6", "success"),
                        ),
                    ),
                )
            )
        with self.assertRaisesRegex(UnsupportedChatContent, "text appeared"):
            _chat_messages(
                (
                    Message(
                        "assistant",
                        (
                            ToolCall("one", "multiply", "{}"),
                            Text("interleaved"),
                            ToolResult("one", "multiply", "6", "success"),
                        ),
                    ),
                )
            )

        api = _FakeCompletions(_response(content="partial", finish_reason="length"))
        client = ChatCompletionsClient(
            SimpleNamespace(chat=SimpleNamespace(completions=api))
        )
        with self.assertRaisesRegex(UnsupportedChatContent, "provider stopped"):
            await client.complete(
                ModelRequest("model-a", (Message("user", (Text("hi"),)),), ())
            )

        api.response = _response(
            tool_calls=[
                SimpleNamespace(
                    type="function",
                    id="call-2",
                    function=SimpleNamespace(name="x", arguments="{}"),
                )
            ],
            finish_reason="tool_calls",
        )
        with self.assertRaisesRegex(UnsupportedChatContent, "unsupported tool call"):
            await client.complete(
                ModelRequest("model-a", (Message("user", (Text("hi"),)),), (), "none")
            )

        api.response = _response(content="partial", finish_reason="unknown")
        with self.assertRaisesRegex(UnsupportedChatContent, "provider stopped"):
            await client.complete(
                ModelRequest("model-a", (Message("user", (Text("hi"),)),), ())
            )

        api.response = _response(
            content="partial", tool_calls=[SimpleNamespace()], finish_reason="stop"
        )
        with self.assertRaisesRegex(UnsupportedChatContent, "finish reason"):
            await client.complete(
                ModelRequest("model-a", (Message("user", (Text("hi"),)),), ())
            )

        api.response = _response(content="reply")
        api.response.choices[0].message.model_extra = {"encrypted_content": "opaque"}
        with self.assertRaisesRegex(UnsupportedChatContent, "encrypted"):
            await client.complete(
                ModelRequest("model-a", (Message("user", (Text("hi"),)),), ())
            )
