"""本地假模型服务验证 HTTP 模型客户端到两种真实 Agent 内核的连接。"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from xuanyue.chat import ChatService
from xuanyue.storage import LocalStore


class NativeKernelChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.requests: list[dict[str, object]] = []
        recorded = self.requests
        self.title_requests: list[dict[str, object]] = []
        title_recorded = self.title_requests
        self.title_started = threading.Event()
        self.release_title = threading.Event()
        title_started = self.title_started
        release_title = self.release_title
        self.first_delta = threading.Event()
        self.release_stream = threading.Event()
        first_delta = self.first_delta
        release_stream = self.release_stream

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(length))
                messages = request["messages"]
                is_title = "会话生成一个简短标题" in str(messages[0].get("content"))
                if is_title:
                    title_recorded.append(request)
                    title_started.set()
                    if "等待手动改名" in str(messages[-1].get("content")):
                        release_title.wait(timeout=3)
                else:
                    recorded.append(request)
                asks_for_multiply = any(
                    "21 单" in str(item.get("content")) for item in messages
                )
                tool_results = [item for item in messages if item.get("role") == "tool"]
                if is_title:
                    message = {
                        "role": "assistant",
                        "content": (
                            "无效\n标题"
                            if "标题失败" in str(messages[-1].get("content"))
                            else "测试会话标题"
                        ),
                    }
                    reason = "stop"
                elif asks_for_multiply and tool_results:
                    assert tool_results[-1]["tool_call_id"] == "test-call-1"
                    assert tool_results[-1]["content"] == "42"
                    message = {"role": "assistant", "content": "42"}
                    reason = "stop"
                elif asks_for_multiply:
                    assert request["tools"][0]["function"]["name"] == "multiply"
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "test-call-1",
                                "type": "function",
                                "function": {
                                    "name": "multiply",
                                    "arguments": '{"orders":21,"units_per_order":2}',
                                },
                            }
                        ],
                    }
                    reason = "tool_calls"
                else:
                    message = {"role": "assistant", "content": "本地答复"}
                    reason = "stop"
                answer = {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "fake-upstream",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": reason,
                        }
                    ],
                }
                if request.get("stream") is True:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()

                    def send(
                        delta: dict[str, object], finished: str | None = None
                    ) -> None:
                        chunk = {
                            "id": "chatcmpl-test",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": "fake-upstream",
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": finished}
                            ],
                        }
                        self.wfile.write(
                            b"data: " + json.dumps(chunk).encode("utf-8") + b"\n\n"
                        )
                        self.wfile.flush()

                    if reason == "tool_calls":
                        call = message["tool_calls"][0]
                        send(
                            {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": call["id"],
                                        "type": "function",
                                        "function": {
                                            "name": "multiply",
                                            "arguments": '{"orders":21,',
                                        },
                                    }
                                ],
                            }
                        )
                        send(
                            {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": '"units_per_order":2}'
                                        },
                                    }
                                ]
                            }
                        )
                    else:
                        content = message["content"]
                        midpoint = len(content) // 2
                        send({"role": "assistant", "content": content[:midpoint]})
                        first_delta.set()
                        if "慢速回复" in str(messages[-1].get("content")):
                            release_stream.wait(timeout=3)
                        send({"content": content[midpoint:]})
                    send({}, reason)
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                body = json.dumps(answer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.thread.start()
        self.config = self.root / "xuanyue.toml"
        self.config.write_text(
            'default_model = "test"\n'
            '[providers.local]\nprotocol = "openai_chat_completions"\n'
            f'base_url = "http://127.0.0.1:{self.provider.server_port}/v1"\n'
            'api_key_env = "XUANYUE_TEST_KEY"\n'
            '[models.test]\nprovider = "local"\nupstream_model = "fake-upstream"\n',
            encoding="utf-8",
        )
        self.environment = patch.dict(
            os.environ, {"XUANYUE_TEST_KEY": "secret-for-test-only"}
        )
        self.environment.start()
        self.store = LocalStore(self.root / "runtime" / "state.sqlite3")
        self.chat = ChatService(self.store, self.config)

    def tearDown(self) -> None:
        self.provider.shutdown()
        self.provider.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.environment.stop()
        self.temporary.cleanup()

    def _await_terminal(self, run_id: str) -> dict[str, object]:
        for _ in range(200):
            run = self.store.run(run_id)
            if run["status"] != "running":
                return run
            time.sleep(0.02)
        self.fail("native kernel did not finish")

    def _await_title_state(self, session_id: str, expected: str) -> None:
        for _ in range(200):
            if self.store.title_state(session_id) == expected:
                return
            time.sleep(0.01)
        self.fail(f"session title did not reach {expected}")

    def test_both_selected_kernels_run_with_local_model_and_persist_trace(self) -> None:
        project = self.store.create_project("本地验证")
        for kernel in ("agentscope", "langgraph"):
            with self.subTest(kernel=kernel):
                session = self.chat.create_session(project["id"], kernel, kernel)
                first = self._await_terminal(
                    self.chat.start_turn(session["id"], "第一问")
                )
                self.assertEqual(first["status"], "completed", first["error_type"])
                self.assertEqual(first["answer"], "本地答复")
                self.assertEqual(first["kernel"], kernel)
                self.assertEqual(
                    [event["seq"] for event in first["events"]],
                    list(range(1, len(first["events"]) + 1)),
                )
                second = self._await_terminal(
                    self.chat.start_turn(session["id"], "第二问")
                )
                self.assertEqual(second["status"], "completed", second["error_type"])
                self.assertGreaterEqual(len(self.requests), 2)
                messages = self.requests[-1]["messages"]
                self.assertTrue(
                    any(item.get("content") == "第一问" for item in messages)
                )
                self.assertTrue(
                    any(item.get("content") == "本地答复" for item in messages)
                )
        self.assertNotIn(b"secret-for-test-only", self.store.path.read_bytes())

    def test_first_successful_turn_generates_title_for_both_kernels_once(self) -> None:
        project = self.store.create_project("自动命名")
        for kernel in ("agentscope", "langgraph"):
            with self.subTest(kernel=kernel):
                session = self.chat.create_session(project["id"], None, kernel)
                self.assertEqual(session["title"], "新会话")
                first = self._await_terminal(
                    self.chat.start_turn(session["id"], "分析本月经营数据")
                )
                self.assertEqual(first["status"], "completed", first["error_type"])
                self._await_title_state(session["id"], "generated")
                self.assertEqual(
                    self.store.session(session["id"])["title"], "测试会话标题"
                )
                detail = self.chat.session_detail(session["id"])
                self.assertEqual(detail["title_state"], "generated")
                self.assertEqual(detail["session"]["title"], "测试会话标题")
                count = len(self.title_requests)
                title_request = self.title_requests[-1]
                self.assertEqual(title_request["model"], "fake-upstream")
                self.assertNotIn("tools", title_request)
                self.assertNotIn("stream", title_request)
                self._await_terminal(self.chat.start_turn(session["id"], "再问一次"))
                self.assertEqual(len(self.title_requests), count)

    def test_title_failure_keeps_completed_answer_and_manual_rename_wins(self) -> None:
        project = self.store.create_project("标题异常")
        failed = self.chat.create_session(project["id"], None, "agentscope")
        run = self._await_terminal(self.chat.start_turn(failed["id"], "标题失败"))
        self.assertEqual(run["status"], "completed", run["error_type"])
        self._await_title_state(failed["id"], "failed")
        self.assertEqual(self.store.session(failed["id"])["title"], "新会话")
        title_count = len(self.title_requests)
        self._await_terminal(self.chat.start_turn(failed["id"], "继续"))
        self.assertEqual(len(self.title_requests), title_count)

        renamed = self.chat.create_session(project["id"], None, "langgraph")
        self.title_started.clear()
        run_id = self.chat.start_turn(renamed["id"], "等待手动改名")
        try:
            self.assertTrue(self.title_started.wait(timeout=3))
            self.store.rename_session(renamed["id"], "用户确定的标题")
        finally:
            self.release_title.set()
        finished = self._await_terminal(run_id)
        self.assertEqual(finished["status"], "completed", finished["error_type"])
        self.assertEqual(self.store.session(renamed["id"])["title"], "用户确定的标题")

    def test_both_kernels_persist_linked_tool_input_and_result(self) -> None:
        project = self.store.create_project("工具链")
        for kernel in ("agentscope", "langgraph"):
            with self.subTest(kernel=kernel):
                session = self.chat.create_session(project["id"], kernel, kernel)
                run = self._await_terminal(
                    self.chat.start_turn(
                        session["id"], "21 单，每单 2 件，请用 multiply 核对。"
                    )
                )
                self.assertEqual(run["status"], "completed", run["error_type"])
                self.assertEqual(run["answer"], "42")
                events = run["events"]
                self.assertEqual(
                    [event["seq"] for event in events],
                    list(range(1, len(events) + 1)),
                )
                by_kind = {
                    kind: [item["payload"] for item in events if item["kind"] == kind]
                    for kind in (
                        "tool_call_started",
                        "tool_call_delta",
                        "tool_call_finished",
                        "tool_result_started",
                        "tool_result_delta",
                        "tool_result_finished",
                    )
                }
                for kind, payloads in by_kind.items():
                    self.assertTrue(payloads, (kernel, kind))
                    self.assertTrue(
                        all(item["tool_call_id"] == "test-call-1" for item in payloads)
                    )
                    if not kind.endswith("delta"):
                        self.assertEqual(len(payloads), 1, (kernel, kind))
                self.assertEqual(
                    by_kind["tool_call_started"][0]["tool_call_name"], "multiply"
                )
                self.assertEqual(
                    json.loads(
                        "".join(item["delta"] for item in by_kind["tool_call_delta"])
                    ),
                    {"orders": 21, "units_per_order": 2},
                )
                self.assertEqual(
                    "".join(item["delta"] for item in by_kind["tool_result_delta"]),
                    "42",
                )
                self.assertEqual(by_kind["tool_result_finished"][0]["state"], "success")
                positions = [
                    next(item["seq"] for item in events if item["kind"] == kind)
                    for kind in by_kind
                ]
                self.assertEqual(positions, sorted(positions))
                tool_messages = [
                    item
                    for item in self.requests[-1]["messages"]
                    if item.get("role") == "tool"
                ]
                self.assertEqual(
                    tool_messages[-1],
                    {"role": "tool", "tool_call_id": "test-call-1", "content": "42"},
                )
                self.assertEqual(
                    sum(event["kind"] == "model_call_started" for event in events),
                    2,
                )

    def test_both_kernels_persist_partial_reply_before_provider_finishes(self) -> None:
        project = self.store.create_project("流式验证")
        for kernel in ("agentscope", "langgraph"):
            with self.subTest(kernel=kernel):
                self.first_delta.clear()
                self.release_stream.clear()
                session = self.chat.create_session(project["id"], kernel, kernel)
                run_id = self.chat.start_turn(session["id"], "请慢速回复")
                try:
                    self.assertTrue(self.first_delta.wait(timeout=3))
                    for _ in range(100):
                        partial = self.store.run(run_id)
                        deltas = [
                            event["payload"]["delta"]
                            for event in partial["events"]
                            if event["kind"] == "text_delta"
                        ]
                        if deltas:
                            break
                        time.sleep(0.01)
                    self.assertEqual(partial["status"], "running")
                    self.assertEqual("".join(deltas), "本地")
                finally:
                    self.release_stream.set()
                final = self._await_terminal(run_id)
                self.assertEqual(final["status"], "completed", final["error_type"])
                self.assertEqual(final["answer"], "本地答复")
                self.assertTrue(self.requests[-1]["stream"])

    def test_both_kernels_send_ordered_current_and_historical_images_to_provider(
        self,
    ) -> None:
        """两图经存储、两种真实框架适配器和 SDK 顺序到达模型请求。"""
        png = b"\x89PNG\r\n\x1a\nprivate-synthetic-image"
        jpeg = b"\xff\xd8\xffprivate-synthetic-jpeg"
        first_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        second_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        self.config.write_text(
            self.config.read_text(encoding="utf-8") + "image_input = true\n",
            encoding="utf-8",
        )
        project = self.store.create_project("图文验证")
        png_attachment = self.store.save_attachment(project["id"], "image/png", png)
        jpeg_attachment = self.store.save_attachment(project["id"], "image/jpeg", jpeg)
        selected = (jpeg_attachment, png_attachment)
        for kernel in ("agentscope", "langgraph"):
            with self.subTest(kernel=kernel):
                session = self.chat.create_session(project["id"], kernel, kernel)
                first = self._await_terminal(
                    self.chat.start_turn(
                        session["id"], "看看图", tuple(item["id"] for item in selected)
                    )
                )
                self.assertEqual(first["status"], "completed", first["error_type"])
                self.assertEqual(first["attachments"], list(selected))
                self.assertEqual(
                    self.requests[-1]["messages"][-1],
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看看图"},
                            {"type": "image_url", "image_url": {"url": first_url}},
                            {"type": "image_url", "image_url": {"url": second_url}},
                        ],
                    },
                )

                second = self._await_terminal(
                    self.chat.start_turn(session["id"], "刚才图里有什么？")
                )
                self.assertEqual(second["status"], "completed", second["error_type"])
                history = self.requests[-1]["messages"]
                self.assertEqual(
                    [item["image_url"]["url"] for item in history[-3]["content"][1:]],
                    [first_url, second_url],
                )
                self.assertEqual(history[-2]["content"], "本地答复")
                self.assertEqual(history[-1]["content"], "刚才图里有什么？")
                self.assertNotIn(
                    b"private-synthetic-image", self.store.path.read_bytes()
                )
                self.assertNotIn(
                    b"private-synthetic-jpeg", self.store.path.read_bytes()
                )
                self.assertNotIn("private-synthetic-image", json.dumps(first["events"]))
