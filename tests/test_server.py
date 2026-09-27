"""本机 HTTP 边界验收；使用合成事件，绝不调用真实模型服务。"""

from __future__ import annotations

import base64
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from xuanyue.chat import ChatService
from xuanyue.server import make_handler
from xuanyue.storage import LocalStore
from xuanyue.types import Event, Image, Task, Text


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big")
        + kind
        + data
        + zlib.crc32(kind + data).to_bytes(4, "big")
    )


# 合成的 1x1 RGBA PNG；可逐字节检查上传、读取与历史回放。
_TEST_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _png_chunk(b"IHDR", b"\x00\x00\x00\x01" * 2 + b"\x08\x06\x00\x00\x00")
    + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    + _png_chunk(b"IEND", b"")
)


class LocalHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.root = root
        self.config = root / "xuanyue.toml"
        self.config.write_text(
            'default_model = "test"\n'
            '[providers.local]\nprotocol = "openai_chat_completions"\n'
            'base_url = "http://127.0.0.1:9191/v1"\n'
            'api_key_env = "XUANYUE_TEST_KEY"\n'
            '[models.test]\nprovider = "local"\nupstream_model = "fake"\n',
            encoding="utf-8",
        )
        self.environment = patch.dict(os.environ, {"XUANYUE_TEST_KEY": "test-value"})
        self.environment.start()
        self.store = LocalStore(root / "runtime" / "state.sqlite3")
        self.tasks: list[Task] = []

        async def fake_stream(task: Task):
            self.tasks.append(task)
            yield Event(task.run_id, 1, "reply_started", {"name": "primary-agent"})
            yield Event(
                task.run_id, 2, "text_delta", {"delta": f"答复{len(self.tasks)}"}
            )
            yield Event(
                task.run_id, 3, "reply_finished", {"finished_reason": "completed"}
            )

        service = ChatService(self.store, self.config, run_stream=fake_stream)
        self.service = service
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.server.RequestHandlerClass = make_handler(
            service, root / "desktop" / "dist", self.server.server_port
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f"127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.environment.stop()
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object], http.client.HTTPMessage]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        headers = extra_headers or {}
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "X-Xuanyue-Client": "desktop-dev",
                **headers,
            }
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        status = response.status
        result = json.loads(response.read().decode("utf-8"))
        response_headers = response.headers
        connection.close()
        return status, result, response_headers

    def request_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, http.client.HTTPMessage]:
        """图片入口返回二进制；拒绝路径仍由测试显式解析 JSON。"""
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request(method, path, body=data, headers=headers or {})
        response = connection.getresponse()
        result = (response.status, response.read(), response.headers)
        connection.close()
        return result

    def upload_image(
        self,
        project_id: str,
        data: bytes = _TEST_PNG,
        media_type: str = "image/png",
    ) -> tuple[int, dict[str, object]]:
        status, body, _ = self.request_bytes(
            "POST",
            f"/api/projects/{project_id}/attachments",
            data,
            {"Content-Type": media_type, "X-Xuanyue-Client": "desktop-dev"},
        )
        return status, json.loads(body)

    def await_run(self, run_id: str) -> dict[str, object]:
        for _ in range(200):
            status, run, _ = self.request("GET", f"/api/runs/{run_id}")
            self.assertEqual(status, 200)
            if run["status"] != "running":
                return run
            time.sleep(0.01)
        self.fail(f"run {run_id} did not reach a terminal status")

    def enable_image_input(self) -> None:
        """只在图片运行测试中声明能力，默认模型仍用于拒绝能力测试。"""
        self.config.write_text(
            self.config.read_text(encoding="utf-8") + "image_input = true\n",
            encoding="utf-8",
        )

    def test_hierarchy_turns_and_completed_history_over_http(self) -> None:
        status, bootstrap, headers = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(bootstrap["projects"], [])
        self.assertEqual(bootstrap["user"]["id"], "local-user")
        self.assertEqual(
            bootstrap["model"],
            {
                "id": "test",
                "configured": True,
                "destination": "127.0.0.1",
                "image_input": False,
            },
        )
        self.assertEqual(bootstrap["models"], [bootstrap["model"]])

        status, project, _ = self.request("POST", "/api/projects", {"name": "项目"})
        self.assertEqual(status, 201)
        status, session, _ = self.request(
            "POST",
            f"/api/projects/{project['id']}/sessions",
            {"title": "对话", "kernel": "langgraph"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(session["kernel"], "langgraph")
        status, listed, _ = self.request(
            "GET", f"/api/projects/{project['id']}/sessions"
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in listed["sessions"]], [session["id"]])

        for index in (1, 2):
            status, started, _ = self.request(
                "POST", f"/api/sessions/{session['id']}/turns", {"text": f"问题{index}"}
            )
            self.assertEqual(status, 202)
            for _ in range(200):
                status, run, _ = self.request("GET", f"/api/runs/{started['run_id']}")
                if run["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["answer"], f"答复{index}")
            self.assertEqual([item["seq"] for item in run["events"]], [1, 2, 3])

        status, detail, _ = self.request("GET", f"/api/sessions/{session['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["runs"]), 2)
        self.assertEqual(
            detail["model_status"],
            {
                "id": "test",
                "configured": True,
                "destination": "127.0.0.1",
                "image_input": False,
            },
        )
        self.assertEqual(len(self.tasks[0].history), 0)
        self.assertEqual(len(self.tasks[1].history), 2)
        self.assertEqual(self.tasks[1].history[0].parts[0].value, "问题1")

    def test_session_reports_saved_nondefault_model_and_missing_binding(self) -> None:
        # 模拟会话建好后默认模型变化，界面仍须按保存的模型展示目标。
        original = self.config.read_text(encoding="utf-8")
        self.config.write_text(
            original
            + '[providers.secondary]\nprotocol = "openai_chat_completions"\n'
            + 'base_url = "https://secondary.example/v1"\n'
            + 'api_key_env = "XUANYUE_TEST_KEY"\n'
            + '[models.secondary]\nprovider = "secondary"\n'
            + 'upstream_model = "second-upstream"\n',
            encoding="utf-8",
        )
        project = self.store.create_project("旧会话")
        _, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(bootstrap["model"]["id"], "test")
        self.assertEqual(
            bootstrap["models"],
            [
                {
                    "id": "test",
                    "configured": True,
                    "destination": "127.0.0.1",
                    "image_input": False,
                },
                {
                    "id": "secondary",
                    "configured": True,
                    "destination": "secondary.example",
                    "image_input": False,
                },
            ],
        )
        self.assertNotIn("api_key_env", json.dumps(bootstrap))
        self.assertNotIn("second-upstream", json.dumps(bootstrap))
        status, session, _ = self.request(
            "POST",
            f"/api/projects/{project['id']}/sessions",
            {"title": "次模型会话", "kernel": "agentscope", "model": "secondary"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(session["model"], "secondary")
        status, invalid, _ = self.request(
            "POST",
            f"/api/projects/{project['id']}/sessions",
            {"title": "不能回退", "kernel": "agentscope", "model": "unknown"},
        )
        self.assertEqual((status, invalid["error"]), (400, "invalid_request"))
        self.assertEqual(len(self.store.sessions(project["id"])), 1)
        status, started, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "测试模型绑定"}
        )
        self.assertEqual(status, 202)
        for _ in range(200):
            _, run, _ = self.request("GET", f"/api/runs/{started['run_id']}")
            if run["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["model"], "secondary")
        self.assertEqual(self.tasks[-1].model, "secondary")
        status, detail, _ = self.request("GET", f"/api/sessions/{session['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["session"]["model"], "secondary")
        self.assertEqual(
            detail["model_status"],
            {
                "id": "secondary",
                "configured": True,
                "destination": "secondary.example",
                "image_input": False,
            },
        )
        # 保存模型从目录移除后不改写历史绑定，也不改用默认模型。
        self.config.write_text(original, encoding="utf-8")
        status, detail, _ = self.request("GET", f"/api/sessions/{session['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["session"]["model"], "secondary")
        self.assertEqual(
            detail["model_status"],
            {
                "id": "secondary",
                "configured": False,
                "destination": None,
                "image_input": False,
            },
        )
        status, error, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "不要偷偷切换模型"}
        )
        self.assertEqual((status, error["error"]), (503, "model_not_configured"))
        self.assertEqual(
            [item["id"] for item in self.store.session_detail(session["id"])["runs"]],
            [started["run_id"]],
        )

    def test_one_image_upload_retrieval_run_and_completed_history(self) -> None:
        self.enable_image_input()
        status, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertTrue(bootstrap["model"]["image_input"])

        status, project, _ = self.request("POST", "/api/projects", {"name": "图文项目"})
        self.assertEqual(status, 201)
        status, session, _ = self.request(
            "POST",
            f"/api/projects/{project['id']}/sessions",
            {"title": "图文会话", "kernel": "agentscope"},
        )
        self.assertEqual(status, 201)
        status, attachment = self.upload_image(project["id"])
        self.assertEqual(status, 201)
        self.assertEqual(attachment["mime_type"], "image/png")
        self.assertEqual(attachment["size"], len(_TEST_PNG))
        self.assertEqual(len(attachment["id"]), 32)

        status, image_bytes, headers = self.request_bytes(
            "GET", f"/api/projects/{project['id']}/attachments/{attachment['id']}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(image_bytes, _TEST_PNG)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")

        status, started, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "图里是什么？", "attachment_ids": [attachment["id"]]},
        )
        self.assertEqual(status, 202)
        first = self.await_run(started["run_id"])
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["attachments"], [attachment])
        self.assertEqual(self.tasks[0].images, (Image("image/png", _TEST_PNG),))
        self.assertEqual(
            self.tasks[0].user_parts,
            (Text("图里是什么？"), Image("image/png", _TEST_PNG)),
        )
        self.assertEqual(self.tasks[0].history, ())

        status, started, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "再看刚才那张图"}
        )
        self.assertEqual(status, 202)
        second = self.await_run(started["run_id"])
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["attachments"], [])
        self.assertEqual(self.tasks[1].images, ())
        self.assertEqual(
            [(item.role, item.parts) for item in self.tasks[1].history],
            [
                ("user", (Text("图里是什么？"), Image("image/png", _TEST_PNG))),
                ("assistant", (Text("答复1"),)),
            ],
        )
        status, detail, _ = self.request("GET", f"/api/sessions/{session['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(
            [run["attachments"] for run in detail["runs"]], [[attachment], []]
        )
        self.assertNotIn(
            base64.b64encode(_TEST_PNG).decode("ascii"), json.dumps(detail)
        )

    def test_attachment_project_isolation_and_single_image_limit(self) -> None:
        self.enable_image_input()
        first = self.store.create_project("甲项目")
        second = self.store.create_project("乙项目")
        session = self.service.create_session(second["id"], "乙会话", "langgraph")
        status, attachment = self.upload_image(first["id"])
        self.assertEqual(status, 201)

        status, error, _ = self.request(
            "GET", f"/api/projects/{second['id']}/attachments/{attachment['id']}"
        )
        self.assertEqual((status, error["error"]), (404, "not_found"))
        status, error, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "跨项目图片", "attachment_ids": [attachment["id"]]},
        )
        self.assertEqual((status, error["error"]), (404, "not_found"))
        status, error, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {
                "text": "两张图片",
                "attachment_ids": [attachment["id"], attachment["id"]],
            },
        )
        self.assertEqual((status, error["error"]), (400, "invalid_request"))
        self.assertEqual(self.store.session_detail(session["id"])["runs"], [])
        self.assertEqual(self.tasks, [])

    def test_unbound_image_delete_respects_project_and_preserves_run_image(
        self,
    ) -> None:
        self.enable_image_input()
        owner = self.store.create_project("附件所属项目")
        other = self.store.create_project("其他项目")
        headers = {"X-Xuanyue-Client": "desktop-dev"}
        status, unbound = self.upload_image(owner["id"])
        self.assertEqual(status, 201)
        owner_path = f"/api/projects/{owner['id']}/attachments/{unbound['id']}"
        other_path = f"/api/projects/{other['id']}/attachments/{unbound['id']}"

        status, response, _ = self.request_bytes("DELETE", owner_path)
        self.assertEqual(
            (status, json.loads(response)["error"]), (400, "invalid_request")
        )
        self.assertEqual(self.request_bytes("GET", owner_path)[0], 200)
        status, response, _ = self.request_bytes("DELETE", other_path, headers=headers)
        self.assertEqual((status, json.loads(response)["error"]), (404, "not_found"))
        self.assertEqual(self.request_bytes("GET", owner_path)[0], 200)
        status, response, _ = self.request_bytes("DELETE", owner_path, headers=headers)
        self.assertEqual((status, response), (204, b""))
        status, error, _ = self.request("GET", owner_path)
        self.assertEqual((status, error["error"]), (404, "not_found"))

        session = self.service.create_session(owner["id"], "已引用图片", "langgraph")
        status, bound = self.upload_image(owner["id"])
        self.assertEqual(status, 201)
        status, started, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "保留这张图", "attachment_ids": [bound["id"]]},
        )
        self.assertEqual(status, 202)
        self.assertEqual(self.await_run(started["run_id"])["status"], "completed")
        bound_path = f"/api/projects/{owner['id']}/attachments/{bound['id']}"
        status, response, _ = self.request_bytes("DELETE", bound_path, headers=headers)
        self.assertEqual(
            (status, json.loads(response)["error"]), (409, "attachment_in_use")
        )
        self.assertEqual(self.request_bytes("GET", bound_path)[:2], (200, _TEST_PNG))
        self.assertEqual(self.store.run(started["run_id"])["attachments"], [bound])

    def test_invalid_image_uploads_do_not_create_attachments(self) -> None:
        project = self.store.create_project("校验项目")
        path = f"/api/projects/{project['id']}/attachments"
        cases = (
            ("empty", b"", "image/png", "desktop-dev", {}),
            ("wrong signature", b"not a png", "image/png", "desktop-dev", {}),
            ("mismatched jpeg", _TEST_PNG, "image/jpeg", "desktop-dev", {}),
            ("unsupported media", _TEST_PNG, "image/gif", "desktop-dev", {}),
            ("missing client header", _TEST_PNG, "image/png", None, {}),
            (
                "oversize declared length",
                _TEST_PNG,
                "image/png",
                "desktop-dev",
                {"Content-Length": str(5 * 1024 * 1024 + 1)},
            ),
        )
        for name, data, media_type, client, extra in cases:
            with self.subTest(name=name):
                headers = {"Content-Type": media_type, **extra}
                if client is not None:
                    headers["X-Xuanyue-Client"] = client
                status, response, _ = self.request_bytes("POST", path, data, headers)
                self.assertEqual(
                    (status, json.loads(response)["error"]), (400, "invalid_request")
                )
        status, error = self.upload_image("missing-project")
        self.assertEqual((status, error["error"]), (404, "not_found"))
        self.assertEqual(
            list((self.root / "runtime" / "attachments").glob("*.bin")), []
        )

    def test_bound_text_model_rejects_image_before_run_reservation(self) -> None:
        self.enable_image_input()
        self.config.write_text(
            self.config.read_text(encoding="utf-8")
            + '[models.text_only]\nprovider = "local"\n'
            + 'upstream_model = "fake-text"\nimage_input = false\n',
            encoding="utf-8",
        )
        project = self.store.create_project("模型能力")
        session = self.service.create_session(
            project["id"], "文本模型会话", "langgraph", "text_only"
        )
        status, attachment = self.upload_image(project["id"])
        self.assertEqual(status, 201)
        status, detail, _ = self.request("GET", f"/api/sessions/{session['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["model_status"]["id"], "text_only")
        self.assertFalse(detail["model_status"]["image_input"])

        status, error, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "请看图", "attachment_ids": [attachment["id"]]},
        )
        self.assertEqual((status, error["error"]), (422, "image_input_not_supported"))
        self.assertEqual(self.store.session_detail(session["id"])["runs"], [])
        self.assertEqual(self.tasks, [])
        status, started, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "只发文字"}
        )
        self.assertEqual(status, 202)
        self.assertEqual(self.await_run(started["run_id"])["status"], "completed")
        self.assertEqual(self.tasks[0].model, "text_only")

    def test_revoked_image_capability_rejects_historical_replay(self) -> None:
        self.enable_image_input()
        project = self.store.create_project("能力变化")
        session = self.service.create_session(project["id"], "历史图片", "agentscope")
        status, attachment = self.upload_image(project["id"])
        self.assertEqual(status, 201)
        status, started, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "先看图", "attachment_ids": [attachment["id"]]},
        )
        self.assertEqual(status, 202)
        self.assertEqual(self.await_run(started["run_id"])["status"], "completed")

        # 会话仍绑定原模型；撤销图片能力后不能暗中重放历史图片。
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "image_input = true", "image_input = false"
            ),
            encoding="utf-8",
        )
        status, error, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "继续讨论图片"}
        )
        self.assertEqual((status, error["error"]), (422, "image_input_not_supported"))
        self.assertEqual(
            [run["id"] for run in self.store.session_detail(session["id"])["runs"]],
            [started["run_id"]],
        )
        self.assertEqual(len(self.tasks), 1)

    def test_post_reservation_and_worker_failures_keep_safe_terminal_trace(
        self,
    ) -> None:
        project = self.store.create_project("失败路径")
        session = self.service.create_session(project["id"], "会话", "agentscope")
        with (
            patch.object(
                self.store, "history", side_effect=RuntimeError("private setup detail")
            ),
            self.assertRaises(RuntimeError),
        ):
            self.service.start_turn(session["id"], "初始化失败")
        first = self.store.session_detail(session["id"])["runs"][0]
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["error_type"], "RuntimeError")
        self.assertEqual(first["events"][0]["kind"], "run_failed")
        self.assertEqual(first["events"][0]["payload"], {"error_type": "RuntimeError"})

        async def failing_stream(task: Task):
            yield Event(task.run_id, 1, "reply_started", {"name": "primary-agent"})
            raise RuntimeError("private provider detail")

        failing = ChatService(self.store, self.config, run_stream=failing_stream)
        run_id = failing.start_turn(session["id"], "执行失败")
        for _ in range(200):
            second = self.store.run(run_id)
            if second["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["answer"], None)
        self.assertEqual(
            [(item["seq"], item["kind"]) for item in second["events"]],
            [(1, "reply_started"), (2, "run_failed")],
        )
        self.assertEqual(
            second["events"][-1]["payload"], {"error_type": "RuntimeError"}
        )
        self.assertNotIn(
            "private provider detail",
            json.dumps(self.store.session_detail(session["id"])),
        )
        next_run = self.store.start_run(session["id"], "下一问", "test")
        self.assertEqual(self.store.history(session["id"], next_run["id"]), ())
        self.store.finish_run(next_run["id"], "incomplete", None, None)

    def test_built_html_has_restrictive_browser_headers(self) -> None:
        assets = self.root / "desktop" / "dist"
        assets.mkdir(parents=True)
        (assets / "index.html").write_text("<!doctype html><title>Test</title>")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request("GET", "/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-src 'none'", csp)
        response.read()
        connection.close()

    def test_write_guards_reject_foreign_origin_missing_header_and_invalid_body(
        self,
    ) -> None:
        status, result, _ = self.request(
            "POST",
            "/api/projects",
            {"name": "项目"},
            {"Origin": "https://untrusted.example"},
        )
        self.assertEqual((status, result["error"]), (403, "forbidden_origin"))
        status, result, _ = self.request("POST", "/api/projects", None)
        self.assertEqual((status, result["error"]), (400, "invalid_request"))
        status, result, _ = self.request("POST", "/api/projects", {"unexpected": "x"})
        self.assertEqual((status, result["error"]), (400, "invalid_request"))
        status, result, _ = self.request(
            "GET", "/api/bootstrap", extra_headers={"Host": "evil.test"}
        )
        self.assertEqual((status, result["error"]), (403, "forbidden_origin"))
        self.assertEqual(self.store.projects(), [])

        status, _, headers = self.request(
            "GET", "/api/bootstrap", extra_headers={"Origin": "http://127.0.0.1:5173"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            headers["Access-Control-Allow-Origin"], "http://127.0.0.1:5173"
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request(
            "OPTIONS",
            "/api/projects",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-xuanyue-client",
            },
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 204)
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "http://127.0.0.1:5173",
        )
        self.assertIn("DELETE", response.headers["Access-Control-Allow-Methods"])
        response.read()
        connection.close()
