"""通过 SDK 的真实 JSON 编码验证输出配额，不请求真实模型服务。"""

from __future__ import annotations

import json
import unittest

import httpx
from openai import AsyncOpenAI, BadRequestError

from tests import test_chat_service as chat_fixture
from xuanyue.llm.openai_compatible import ChatCompletionsClient, UnsupportedChatContent
from xuanyue.types import Message, ModelReply, ModelRequest, ReasoningOption, Text

_REQUEST = ModelRequest(
    "user-configured-model", (Message("user", (Text("请计算二加三。"),)),), ()
)


def _response(streaming: bool, finish_reason: str = "stop") -> httpx.Response:
    """同一合成回复同时提供 JSON 和 SSE 表示，让两条调用路径接受相同检查。"""
    message = {"role": "assistant", "content": "5"}
    value = {
        "id": "synthetic-completion",
        "created": 0,
        "object": "chat.completion.chunk" if streaming else "chat.completion",
        "model": _REQUEST.model,
        "choices": [
            {
                "index": 0,
                "delta" if streaming else "message": message,
                "finish_reason": finish_reason,
            }
        ],
    }
    if streaming:
        return httpx.Response(
            200,
            content=("data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n").encode(),
            headers={"content-type": "text/event-stream"},
        )
    return httpx.Response(200, json=value)


def _sdk(response: httpx.Response, requests: list[dict]) -> AsyncOpenAI:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return response

    return AsyncOpenAI(
        api_key="synthetic-test-key",
        base_url="https://synthetic.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )


class ModelOutputLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_selected_limit_reaches_stream_and_complete_requests(
        self,
    ) -> None:
        for parameter in ("max_tokens", "max_completion_tokens"):
            for streaming in (False, True):
                with self.subTest(parameter=parameter, streaming=streaming):
                    requests: list[dict] = []
                    async with _sdk(_response(streaming), requests) as sdk:
                        client = ChatCompletionsClient(
                            sdk,
                            ReasoningOption("high", "高", effort="high"),
                            max_output_tokens=2048,
                            output_token_parameter=parameter,
                        )
                        if streaming:
                            chunks = [chunk async for chunk in client.stream(_REQUEST)]
                            reply = chunks[-1].reply
                        else:
                            reply = await client.complete(_REQUEST)
                    self.assertEqual(reply, ModelReply((Text("5"),)))
                    self.assertEqual(len(requests), 1)
                    sent = requests[0]
                    self.assertEqual(
                        {
                            key: sent[key]
                            for key in ("max_tokens", "max_completion_tokens")
                            if key in sent
                        },
                        {parameter: 2048},
                    )
                    self.assertEqual(sent["reasoning_effort"], "high")
                    self.assertEqual(sent.get("stream", False), streaming)
                    self.assertNotIn("max_input_tokens", sent)

    async def test_unset_limit_preserves_provider_default(self) -> None:
        for parameter in ("max_tokens", "max_completion_tokens"):
            for streaming in (False, True):
                with self.subTest(parameter=parameter, streaming=streaming):
                    requests: list[dict] = []
                    async with _sdk(_response(streaming), requests) as sdk:
                        client = ChatCompletionsClient(
                            sdk, output_token_parameter=parameter
                        )
                        if streaming:
                            _ = [chunk async for chunk in client.stream(_REQUEST)]
                        else:
                            await client.complete(_REQUEST)
                    self.assertNotIn("max_tokens", requests[0])
                    self.assertNotIn("max_completion_tokens", requests[0])

    def test_invalid_limits_fail_before_sdk_access(self) -> None:
        for limit in (True, False, 0, -1, 1.5, "2048", [], 2147483648):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                ChatCompletionsClient(None, max_output_tokens=limit)
        for parameter in (None, "max_input_tokens", "max_output_tokens", []):
            with self.subTest(parameter=parameter), self.assertRaises(ValueError):
                ChatCompletionsClient(None, output_token_parameter=parameter)

    async def test_length_finish_never_becomes_completed_reply(self) -> None:
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests: list[dict] = []
                received = []
                async with _sdk(_response(streaming, "length"), requests) as sdk:
                    client = ChatCompletionsClient(sdk, max_output_tokens=1)
                    with self.assertRaisesRegex(UnsupportedChatContent, "length"):
                        if streaming:
                            async for chunk in client.stream(_REQUEST):
                                received.append(chunk)
                        else:
                            await client.complete(_REQUEST)
                self.assertEqual(requests[0]["max_tokens"], 1)
                self.assertTrue(all(chunk.reply is None for chunk in received))

    async def test_context_rejection_does_not_retry_with_truncated_input(self) -> None:
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests: list[dict] = []
                response = httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "Synthetic context limit exceeded",
                            "type": "invalid_request_error",
                            "code": "context_length_exceeded",
                        }
                    },
                )
                async with _sdk(response, requests) as sdk:
                    client = ChatCompletionsClient(sdk, max_output_tokens=2048)
                    with self.assertRaises(BadRequestError):
                        if streaming:
                            _ = [chunk async for chunk in client.stream(_REQUEST)]
                        else:
                            await client.complete(_REQUEST)
                self.assertEqual(len(requests), 1)
                self.assertEqual(
                    requests[0]["messages"],
                    [{"role": "user", "content": "请计算二加三。"}],
                )


class NativeKernelOutputLimitsTests(unittest.TestCase):
    """复用假 HTTP 服务，经配置、ChatService 和真实内核验证每次请求的配额。"""

    def setUp(self) -> None:
        # 组合 fixture，不继承或导入 TestCase 类，避免 unittest 重复收集它的测试。
        self.native = chat_fixture.NativeKernelChatTests()
        self.native.setUp()
        self.addCleanup(self.native.tearDown)
        self.original_config = self.native.config.read_text(encoding="utf-8")

    def _configure_limits(self, parameter: str, output_tokens: int) -> None:
        """输入容量故意小于问题大小，确认它是元数据，不会截掉输入或外发。"""
        self.native.config.write_text(
            self.original_config
            + "max_input_tokens = 1\n"
            + f"max_output_tokens = {output_tokens}\n"
            + f'output_token_parameter = "{parameter}"\n',
            encoding="utf-8",
        )

    def _assert_request_limit(
        self, request: dict, parameter: str, output_tokens: int
    ) -> None:
        self.assertEqual(
            {
                key: request[key]
                for key in ("max_tokens", "max_completion_tokens")
                if key in request
            },
            {parameter: output_tokens},
        )
        self.assertNotIn("max_input_tokens", request)

    def test_both_kernels_apply_configured_limit_to_tool_turn_and_title(self) -> None:
        native = self.native
        project = native.store.create_project("输出配额验收")
        for kernel in ("agentscope", "langgraph"):
            for parameter in ("max_tokens", "max_completion_tokens"):
                with self.subTest(kernel=kernel, parameter=parameter):
                    self._configure_limits(parameter, 2048)
                    session = native.chat.create_session(project["id"], None, kernel)
                    before = len(native.requests)
                    titles_before = len(native.title_requests)
                    run = native._await_terminal(
                        native.chat.start_turn(session["id"], "21 单，每单 2 件")
                    )
                    self.assertEqual(run["status"], "completed", run["error_type"])
                    self.assertEqual(run["answer"], "42")
                    self.assertEqual(run["kernel"], kernel)
                    native._await_title_state(session["id"], "generated")
                    turn_requests = native.requests[before:]
                    title_requests = native.title_requests[titles_before:]
                    self.assertEqual(len(turn_requests), 2)
                    self.assertEqual(len(title_requests), 1)
                    for request in [*turn_requests, *title_requests]:
                        self._assert_request_limit(request, parameter, 2048)
                    self.assertTrue(all(r["stream"] for r in turn_requests))
                    self.assertNotIn("stream", title_requests[0])
                    self.assertTrue(
                        any(
                            message.get("content") == "21 单，每单 2 件"
                            for message in turn_requests[0]["messages"]
                        )
                    )
                    self.assertTrue(
                        any(
                            message.get("role") == "tool"
                            and message.get("content") == "42"
                            for message in turn_requests[1]["messages"]
                        )
                    )

    def test_config_edit_during_tool_turn_takes_effect_only_on_next_turn(self) -> None:
        native = self.native
        project = native.store.create_project("运行中冻结输出配额")
        for kernel in ("agentscope", "langgraph"):
            for parameter in ("max_tokens", "max_completion_tokens"):
                with self.subTest(kernel=kernel, parameter=parameter):
                    self._configure_limits(parameter, 2048)
                    next_parameter = (
                        "max_completion_tokens"
                        if parameter == "max_tokens"
                        else "max_tokens"
                    )
                    native.hold_tool.set()
                    native.tool_started.clear()
                    native.release_tool.clear()
                    session = native.chat.create_session(project["id"], None, kernel)
                    before = len(native.requests)
                    titles_before = len(native.title_requests)
                    run_id = native.chat.start_turn(session["id"], "21 单，每单 2 件")
                    try:
                        self.assertTrue(native.tool_started.wait(timeout=5))
                        self._configure_limits(next_parameter, 512)
                    finally:
                        native.release_tool.set()
                        native.hold_tool.clear()
                    run = native._await_terminal(run_id)
                    self.assertEqual(run["status"], "completed", run["error_type"])
                    self.assertEqual(run["answer"], "42")
                    native._await_title_state(session["id"], "generated")
                    turn_requests = native.requests[before:]
                    title_requests = native.title_requests[titles_before:]
                    self.assertEqual(len(turn_requests), 2)
                    self.assertEqual(len(title_requests), 1)
                    for request in [*turn_requests, *title_requests]:
                        self._assert_request_limit(request, parameter, 2048)

                    # 下一轮必须采用新值；否则“冻结成功”也可能只是配置从未被重读。
                    next_before = len(native.requests)
                    next_run = native._await_terminal(
                        native.chat.start_turn(session["id"], "继续")
                    )
                    self.assertEqual(
                        next_run["status"], "completed", next_run["error_type"]
                    )
                    self.assertGreater(len(native.requests), next_before)
                    for request in native.requests[next_before:]:
                        self._assert_request_limit(request, next_parameter, 512)
                    self.assertEqual(len(native.title_requests), titles_before + 1)


if __name__ == "__main__":
    unittest.main()
