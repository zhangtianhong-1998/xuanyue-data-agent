"""本地假模型服务验证 HTTP 模型客户端到两种真实 Agent 内核的连接。"""

from __future__ import annotations

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

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(length))
                recorded.append(request)
                messages = request["messages"]
                asks_for_multiply = any(
                    "21 单" in str(item.get("content")) for item in messages
                )
                tool_results = [item for item in messages if item.get("role") == "tool"]
                if asks_for_multiply and tool_results:
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
