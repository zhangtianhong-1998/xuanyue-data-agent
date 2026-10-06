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

    def configure_reasoning_models(self) -> None:
        """两个模型有不同原生选项，确保切换不能误沿用旧模型档位。"""
        self.config.write_text(
            self.config.read_text(encoding="utf-8")
            + 'default_reasoning = "high"\n'
            + 'reasoning_options = [{id="high", label="深入分析", effort="high"}]\n'
            + '[models.vision]\nprovider = "local"\nupstream_model = "fake-vision"\n'
            + 'image_input = true\ndefault_reasoning = "think"\n'
            + 'reasoning_options = [{id="think", label="开启思考", thinking="enabled"}]\n',
            encoding="utf-8",
        )

    def test_model_switch_uses_target_default_and_preserves_old_run_snapshot(
        self,
    ) -> None:
        self.configure_reasoning_models()
        project = self.store.create_project("切换模型")
        session = self.service.create_session(project["id"], "连续会话", "agentscope")
        self.assertEqual((session["model"], session["reasoning"]), ("test", "high"))
        _, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(
            bootstrap["models"][1],
            {
                "id": "vision",
                "configured": True,
                "destination": "127.0.0.1",
                "image_input": True,
                "provider": "local",
                "upstream_model": "fake-vision",
                "reasoning_options": [
                    {
                        "id": "think",
                        "label": "开启思考",
                        "thinking": "enabled",
                    }
                ],
                "default_reasoning": "think",
            },
        )
        status, started, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "第一问"}
        )
        self.assertEqual(status, 202)
        original_run = self.await_run(started["run_id"])
        self.assertEqual(original_run["model"], "test")
        self.assertEqual(original_run["reasoning"], "high")
        self.assertEqual(
            original_run["reasoning_config"],
            {"id": "high", "label": "深入分析", "effort": "high", "thinking": None},
        )

        status, changed, _ = self.request(
            "PATCH", f"/api/sessions/{session['id']}/model", {"model": "vision"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(changed["session"]["kernel"], "agentscope")
        self.assertEqual(changed["session"]["model"], "vision")
        self.assertEqual(changed["session"]["reasoning"], "think")
        self.assertEqual(changed["runs"], [original_run])
        self.assertEqual(self.tasks[0].model, "test")

        status, next_run, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "延续第一问"}
        )
        self.assertEqual(status, 202)
        completed = self.await_run(next_run["run_id"])
        self.assertEqual(completed["model"], "vision")
        self.assertEqual(completed["reasoning"], "think")
        self.assertEqual(
            completed["reasoning_config"],
            {"id": "think", "label": "开启思考", "effort": None, "thinking": "enabled"},
        )
        self.assertEqual(self.tasks[-1].model, "vision")
        self.assertEqual(self.tasks[-1].history[0].parts[0], Text("第一问"))
        self.assertEqual(self.store.run(original_run["id"]), original_run)

        # 修改目录或当前选择不能回写已经开始的运行参数。
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                'effort="high"', 'effort="low"'
            ),
            encoding="utf-8",
        )
        status, changed, _ = self.request(
            "PATCH",
            f"/api/sessions/{session['id']}/model",
            {"model": "vision", "reasoning": "default"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(changed["session"]["reasoning"], "default")
        self.assertEqual(changed["runs"], [original_run, completed])
        _, last, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "沿用供应商默认"}
        )
        self.assertEqual(self.await_run(last["run_id"])["reasoning_config"], {})

    def test_invalid_model_selection_does_not_change_session_or_reserve_run(
        self,
    ) -> None:
        self.configure_reasoning_models()
        project = self.store.create_project("拒绝错误选择")
        session = self.service.create_session(project["id"], "会话", "langgraph")
        path = f"/api/sessions/{session['id']}/model"
        variants = (
            {"model": "missing"},
            {"model": "vision", "reasoning": "high"},
            {"model": "vision", "reasoning": None},
            {"model": "vision", "reasoning": {"effort": "high"}},
            {"model": "vision", "reasoning": "think", "extra_body": {"custom": True}},
            {"reasoning": "think"},
            {"model": "vision", "kernel": "agentscope"},
        )
        for body in variants:
            with self.subTest(body=body):
                status, error, _ = self.request("PATCH", path, body)
                self.assertEqual((status, error["error"]), (400, "invalid_request"))
                self.assertEqual(self.store.session(session["id"]), session)
                self.assertEqual(self.store.session_detail(session["id"])["runs"], [])
        self.assertEqual(self.tasks, [])

    def test_model_switch_is_blocked_while_run_is_active(self) -> None:
        self.configure_reasoning_models()
        project = self.store.create_project("运行中的选择")
        session = self.service.create_session(project["id"], "运行中", "agentscope")
        run = self.store.start_run(session["id"], "处理中", "test", reasoning="high")
        before = self.store.session(session["id"])
        status, error, _ = self.request(
            "PATCH", f"/api/sessions/{session['id']}/model", {"model": "vision"}
        )
        self.assertEqual((status, error["error"]), (409, "session_busy"))
        self.assertEqual(self.store.session(session["id"]), before)
        self.assertEqual(self.store.run(run["id"])["status"], "running")

    def test_completed_image_history_prevents_switch_to_text_model(self) -> None:
        self.configure_reasoning_models()
        project = self.store.create_project("图片历史")
        session = self.service.create_session(
            project["id"], "看图", "langgraph", "vision"
        )
        status, attachment = self.upload_image(project["id"])
        self.assertEqual(status, 201)
        status, started, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "描述图片", "attachment_ids": [attachment["id"]]},
        )
        self.assertEqual(status, 202)
        original_run = self.await_run(started["run_id"])
        self.assertEqual(original_run["status"], "completed")
        before = self.store.session(session["id"])

        status, error, _ = self.request(
            "PATCH", f"/api/sessions/{session['id']}/model", {"model": "test"}
        )
        self.assertEqual((status, error["error"]), (422, "image_input_not_supported"))
        self.assertEqual(self.store.session(session["id"]), before)
        self.assertEqual(self.store.run(original_run["id"]), original_run)
        self.assertEqual(
            self.store.images_for_run(original_run["id"]),
            (Image("image/png", _TEST_PNG),),
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
                "provider": "local",
                "upstream_model": "fake",
                "reasoning_options": [],
                "default_reasoning": "default",
            },
        )
        self.assertEqual(bootstrap["models"], [bootstrap["model"]])

        status, project, _ = self.request(
            "POST", "/api/projects", {"name": "项目", "workspace_path": str(self.root)}
        )
        self.assertEqual(status, 201)
        self.assertEqual(project["workspace_path"], str(self.root.resolve()))
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
                "provider": "local",
                "upstream_model": "fake",
                "reasoning_options": [],
                "default_reasoning": "default",
            },
        )
        self.assertEqual(len(self.tasks[0].history), 0)
        self.assertEqual(len(self.tasks[1].history), 2)
        self.assertEqual(self.tasks[1].history[0].parts[0].value, "问题1")

    def test_new_session_without_title_is_ready_for_first_turn(self) -> None:
        _, project, _ = self.request(
            "POST", "/api/projects", {"name": "项目", "workspace_path": str(self.root)}
        )
        status, session, _ = self.request(
            "POST", f"/api/projects/{project['id']}/sessions", {"kernel": "agentscope"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(session["title"], "新会话")
        self.assertEqual(session["kernel"], "agentscope")
        status, detail, _ = self.request("GET", f"/api/sessions/{session['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["title_state"], "pending")
        status, started, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "分析订单"}
        )
        self.assertEqual(status, 202)
        self.assertEqual(self.await_run(started["run_id"])["status"], "completed")

    def test_rename_project_and_session_over_http_keeps_existing_run(self) -> None:
        _, project, _ = self.request(
            "POST",
            "/api/projects",
            {"name": "旧项目", "workspace_path": str(self.root)},
        )
        _, session, _ = self.request(
            "POST",
            f"/api/projects/{project['id']}/sessions",
            {"title": "旧会话", "kernel": "langgraph"},
        )
        _, accepted, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "保留的问题"}
        )
        original_run = self.await_run(accepted["run_id"])

        status, renamed_project, _ = self.request(
            "PATCH", f"/api/projects/{project['id']}", {"name": "新项目"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            renamed_project,
            {
                "id": project["id"],
                "name": "新项目",
                "workspace_path": str(self.root.resolve()),
                "created_at": project["created_at"],
            },
        )
        status, renamed_session, _ = self.request(
            "PATCH", f"/api/sessions/{session['id']}", {"title": "新会话"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed_session["title"], "新会话")
        self.assertEqual(renamed_session["kernel"], session["kernel"])
        self.assertEqual(renamed_session["model"], session["model"])

        _, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(bootstrap["projects"], [renamed_project])
        _, listed, _ = self.request("GET", f"/api/projects/{project['id']}/sessions")
        self.assertEqual(listed["sessions"], [renamed_session])
        _, detail, _ = self.request("GET", f"/api/sessions/{session['id']}")
        self.assertEqual(detail["session"], renamed_session)
        self.assertEqual(detail["runs"], [original_run])
        _, again, _ = self.request(
            "POST", f"/api/sessions/{session['id']}/turns", {"text": "下一问"}
        )
        self.assertEqual(self.await_run(again["run_id"])["status"], "completed")
        self.assertEqual(self.tasks[-1].history[0].parts[0].value, "保留的问题")

    def test_rename_rejects_invalid_unknown_foreign_and_untrusted_requests(
        self,
    ) -> None:
        project = self.store.create_project("本机项目")
        session = self.service.create_session(project["id"], "本机会话", "agentscope")
        project_path = f"/api/projects/{project['id']}"
        session_path = f"/api/sessions/{session['id']}"
        for path, body in (
            (project_path, {}),
            (project_path, {"name": "  "}),
            (project_path, {"name": "x" * 101}),
            (project_path, {"name": "新", "kernel": "langgraph"}),
            (session_path, {"title": "  "}),
            (session_path, {"title": "x" * 201}),
            (session_path, {"title": "新", "model": "other"}),
        ):
            with self.subTest(path=path, body=body):
                status, error, _ = self.request("PATCH", path, body)
                self.assertEqual((status, error["error"]), (400, "invalid_request"))

        with self.store._connection() as db:
            db.execute(
                "INSERT INTO users(id,name) VALUES (?,?)", ("other-user", "其他用户")
            )
            db.execute(
                "INSERT INTO projects(id,user_id,name,created_at) VALUES (?,?,?,?)",
                ("other-project", "other-user", "私有项目", project["created_at"]),
            )
            db.execute(
                "INSERT INTO sessions(id,project_id,title,kernel,model,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    "other-session",
                    "other-project",
                    "私有会话",
                    "langgraph",
                    None,
                    session["created_at"],
                    session["updated_at"],
                ),
            )
        for path, body in (
            ("/api/projects/unknown", {"name": "新名"}),
            ("/api/projects/other-project", {"name": "新名"}),
            ("/api/sessions/unknown", {"title": "新名"}),
            ("/api/sessions/other-session", {"title": "新名"}),
        ):
            with self.subTest(path=path):
                status, error, _ = self.request("PATCH", path, body)
                self.assertEqual((status, error["error"]), (404, "not_found"))

        status, error, _ = self.request(
            "PATCH",
            project_path,
            {"name": "不可信"},
            {"Origin": "https://untrusted.example"},
        )
        self.assertEqual((status, error["error"]), (403, "forbidden_origin"))
        status, response, _ = self.request_bytes(
            "PATCH",
            project_path,
            b'{"name":"missing-client"}',
            {"Content-Type": "application/json"},
        )
        self.assertEqual(
            (status, json.loads(response)["error"]), (400, "invalid_request")
        )
        self.assertEqual(self.store.projects(), [project])
        self.assertEqual(self.store.session(session["id"]), session)

    def test_delete_session_and_project_over_http(self) -> None:
        _, project, _ = self.request(
            "POST",
            "/api/projects",
            {"name": "待删项目", "workspace_path": str(self.root)},
        )
        _, session, _ = self.request(
            "POST",
            f"/api/projects/{project['id']}/sessions",
            {"title": "待删会话", "kernel": "langgraph"},
        )
        self.enable_image_input()
        status, image = self.upload_image(project["id"])
        self.assertEqual(status, 201)
        _, started, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "看图", "attachment_ids": [image["id"]]},
        )
        self.assertEqual(self.await_run(started["run_id"])["status"], "completed")
        path = self.root / "runtime" / "attachments" / f"{image['id']}.bin"
        self.assertTrue(path.exists())

        status, body, _ = self.request_bytes(
            "DELETE",
            f"/api/sessions/{session['id']}",
            headers={"X-Xuanyue-Client": "desktop-dev"},
        )
        self.assertEqual((status, body), (204, b""))
        self.assertFalse(path.exists())
        status, body, _ = self.request("GET", f"/api/runs/{started['run_id']}")
        self.assertEqual((status, body["error"]), (404, "not_found"))

        draft = self.store.save_attachment(project["id"], "image/png", _TEST_PNG)
        draft_path = self.root / "runtime" / "attachments" / f"{draft['id']}.bin"
        status, body, _ = self.request_bytes(
            "DELETE",
            f"/api/projects/{project['id']}",
            headers={"X-Xuanyue-Client": "desktop-dev"},
        )
        self.assertEqual((status, body), (204, b""))
        self.assertFalse(draft_path.exists())
        status, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual((status, bootstrap["projects"]), (200, []))
        status, error, _ = self.request(
            "POST",
            f"/api/projects/{project['id']}/sessions",
            {"title": "迟到会话", "kernel": "langgraph"},
        )
        self.assertEqual((status, error["error"]), (404, "not_found"))

    def test_delete_reports_pending_file_cleanup_after_db_commit(self) -> None:
        project = self.store.create_project("待删除文件")
        image = self.store.save_attachment(project["id"], "image/png", _TEST_PNG)
        path = self.root / "runtime" / "attachments" / f"{image['id']}.bin"
        with patch.object(Path, "unlink", side_effect=OSError("synthetic failure")):
            status, body, _ = self.request_bytes(
                "DELETE",
                f"/api/projects/{project['id']}",
                headers={"X-Xuanyue-Client": "desktop-dev"},
            )
        self.assertEqual(
            (status, json.loads(body)),
            (202, {"status": "deleted", "attachment_cleanup": "pending"}),
        )
        self.assertEqual(self.store.projects(), [])
        self.assertTrue(path.exists())

        next_project = self.store.create_project("触发重试")
        status, body, _ = self.request_bytes(
            "DELETE",
            f"/api/projects/{next_project['id']}",
            headers={"X-Xuanyue-Client": "desktop-dev"},
        )
        self.assertEqual((status, body), (204, b""))
        self.assertFalse(path.exists())

    def test_delete_rejects_busy_missing_foreign_and_untrusted_requests(self) -> None:
        project = self.store.create_project("本机项目")
        session = self.store.create_session(
            project["id"], "本机会话", "agentscope", "test"
        )
        active = self.store.start_run(session["id"], "运行中", "test")
        headers = {"X-Xuanyue-Client": "desktop-dev"}
        for path in (
            f"/api/sessions/{session['id']}",
            f"/api/projects/{project['id']}",
        ):
            status, body, _ = self.request_bytes("DELETE", path, headers=headers)
            self.assertEqual((status, json.loads(body)["error"]), (409, "session_busy"))
        self.assertEqual(self.store.run(active["id"])["status"], "running")

        with self.store._connection() as db:
            db.execute("INSERT INTO users(id,name) VALUES (?,?)", ("other", "其他用户"))
            db.execute(
                "INSERT INTO projects(id,user_id,name,created_at) VALUES (?,?,?,?)",
                ("foreign-project", "other", "私有项目", project["created_at"]),
            )
            db.execute(
                "INSERT INTO sessions(id,project_id,title,kernel,model,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    "foreign-session",
                    "foreign-project",
                    "私有会话",
                    "langgraph",
                    None,
                    session["created_at"],
                    session["updated_at"],
                ),
            )
        for path in (
            "/api/projects/missing",
            "/api/projects/foreign-project",
            "/api/sessions/missing",
            "/api/sessions/foreign-session",
        ):
            status, body, _ = self.request_bytes("DELETE", path, headers=headers)
            self.assertEqual((status, json.loads(body)["error"]), (404, "not_found"))

        project_path = f"/api/projects/{project['id']}"
        status, body, _ = self.request_bytes("DELETE", project_path)
        self.assertEqual((status, json.loads(body)["error"]), (400, "invalid_request"))
        status, body, _ = self.request_bytes(
            "DELETE",
            project_path,
            headers={**headers, "Origin": "https://untrusted.example"},
        )
        self.assertEqual((status, json.loads(body)["error"]), (403, "forbidden_origin"))
        status, body, _ = self.request_bytes(
            "DELETE", project_path, headers={**headers, "Host": "wrong.example"}
        )
        self.assertEqual((status, json.loads(body)["error"]), (403, "forbidden_origin"))
        self.assertEqual(self.store.run(active["id"])["status"], "running")

    def test_project_creation_requires_existing_absolute_directory(self) -> None:
        for body in (
            {"name": "缺目录"},
            {"name": "相对路径", "workspace_path": "relative"},
            {"name": "目录不存在", "workspace_path": str(self.root / "missing")},
            {"name": "这是文件", "workspace_path": str(self.config)},
            {"name": "类型不对", "workspace_path": 123},
        ):
            with self.subTest(body=body):
                status, result, _ = self.request("POST", "/api/projects", body)
                self.assertEqual((status, result["error"]), (400, "invalid_request"))
        self.assertEqual(self.store.projects(), [])

        status, created, _ = self.request(
            "POST",
            "/api/projects",
            {"name": "工作目录", "workspace_path": str(self.root / "runtime" / "..")},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["workspace_path"], str(self.root.resolve()))
        status, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(bootstrap["projects"], [created])

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
                    "provider": "local",
                    "upstream_model": "fake",
                    "reasoning_options": [],
                    "default_reasoning": "default",
                },
                {
                    "id": "secondary",
                    "configured": True,
                    "destination": "secondary.example",
                    "image_input": False,
                    "provider": "secondary",
                    "upstream_model": "second-upstream",
                    "reasoning_options": [],
                    "default_reasoning": "default",
                },
            ],
        )
        self.assertNotIn("api_key_env", json.dumps(bootstrap))
        self.assertNotIn("XUANYUE_TEST_KEY", json.dumps(bootstrap))
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
                "provider": "secondary",
                "upstream_model": "second-upstream",
                "reasoning_options": [],
                "default_reasoning": "default",
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

        status, project, _ = self.request(
            "POST",
            "/api/projects",
            {"name": "图文项目", "workspace_path": str(self.root)},
        )
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

    def test_four_image_turn_keeps_order_and_rejects_fifth_or_duplicate(self) -> None:
        self.enable_image_input()
        project = self.store.create_project("多图项目")
        session = self.service.create_session(project["id"], "多图会话", "langgraph")
        uploaded = [self.upload_image(project["id"])[1] for _ in range(5)]
        selected = [uploaded[i] for i in (3, 1, 0, 2)]
        turn_path = f"/api/sessions/{session['id']}/turns"
        status, started, _ = self.request(
            "POST",
            turn_path,
            {
                "text": "按顺序看这四张图",
                "attachment_ids": [item["id"] for item in selected],
            },
        )
        self.assertEqual(status, 202)
        first = self.await_run(started["run_id"])
        self.assertEqual(first["attachments"], selected)
        self.assertEqual(len(self.tasks[0].images), 4)

        for rejected in (
            [item["id"] for item in uploaded],
            [uploaded[0]["id"], uploaded[0]["id"]],
        ):
            status, error, _ = self.request(
                "POST", turn_path, {"text": "不应开始", "attachment_ids": rejected}
            )
            self.assertEqual((status, error["error"]), (400, "invalid_request"))
        status, started, _ = self.request("POST", turn_path, {"text": "记得顺序吗"})
        self.assertEqual(status, 202)
        self.assertEqual(self.await_run(started["run_id"])["status"], "completed")
        self.assertEqual(
            self.tasks[1].history[0].parts,
            (
                Text("按顺序看这四张图"),
                *(Image("image/png", _TEST_PNG) for _ in range(4)),
            ),
        )
        self.assertEqual(len(self.store.session_detail(session["id"])["runs"]), 2)

    def test_attachment_project_isolation_and_duplicate_image_limit(self) -> None:
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
        own = self.store.save_attachment(second["id"], "image/png", _TEST_PNG)
        status, error, _ = self.request(
            "POST",
            f"/api/sessions/{session['id']}/turns",
            {"text": "混入跨项目图片", "attachment_ids": [own["id"], attachment["id"]]},
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
        self.assertIn("PATCH", response.headers["Access-Control-Allow-Methods"])
        response.read()
        connection.close()

    def test_model_settings_http_updates_are_redacted_and_immediate(self) -> None:
        status, original, _ = self.request("GET", "/api/model-config")
        self.assertEqual(status, 200)
        self.assertEqual(original["default_model"], "test")
        self.assertEqual(original["providers"][0]["key_configured"], True)
        self.assertNotIn("test-value", json.dumps(original))

        updated = {
            "default_model": "vision",
            "providers": [
                {
                    "id": "local",
                    "protocol": "openai_chat_completions",
                    "base_url": "http://127.0.0.1:9191/v1",
                },
                {
                    "id": "vision",
                    "protocol": "openai_chat_completions",
                    "base_url": "https://vision.example.invalid/v1",
                    "api_key": "private-new-key",
                },
            ],
            "models": [
                {
                    "id": "test",
                    "provider": "local",
                    "upstream_model": "fake",
                    "image_input": False,
                },
                {
                    "id": "vision",
                    "provider": "vision",
                    "upstream_model": "vision-upstream",
                    "image_input": True,
                },
            ],
        }
        status, result, _ = self.request("PUT", "/api/model-config", updated)
        self.assertEqual(status, 200)
        self.assertEqual(result["default_model"], "vision")
        self.assertTrue(result["providers"][1]["key_configured"])
        self.assertNotIn("private-new-key", json.dumps(result))
        _, bootstrap, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(bootstrap["model"]["id"], "vision")
        self.assertTrue(bootstrap["model"]["image_input"])
        _, project, _ = self.request(
            "POST", "/api/projects", {"name": "项目", "workspace_path": str(self.root)}
        )
        status, session, _ = self.request(
            "POST", f"/api/projects/{project['id']}/sessions", {"kernel": "langgraph"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(session["model"], "vision")
        self.assertEqual(self.service._model_binding("vision")[1], "private-new-key")

        rotated = {
            **updated,
            "providers": [
                updated["providers"][0],
                {**updated["providers"][1], "api_key": "rotated-private-key"},
            ],
        }
        status, result, _ = self.request("PUT", "/api/model-config", rotated)
        self.assertEqual(status, 200)
        self.assertEqual(
            self.service._model_binding("vision")[1], "rotated-private-key"
        )
        self.assertNotIn("rotated-private-key", json.dumps(result))
        managed = self.root / ".env.xuanyue.models"
        self.assertNotIn("private-new-key", managed.read_text(encoding="utf-8"))

        # 已有会话引用的产品模型不能从目录中删除。
        removal = {
            **updated,
            "default_model": "test",
            "models": [updated["models"][0]],
            "providers": [updated["providers"][0]],
        }
        before = self.config.read_bytes()
        status, error, _ = self.request("PUT", "/api/model-config", removal)
        self.assertEqual((status, error["error"]), (409, "model_config_conflict"))
        self.assertEqual(self.config.read_bytes(), before)

        status, error, _ = self.request(
            "PUT", "/api/model-config", {**updated, "extra": "rejected"}
        )
        self.assertEqual((status, error["error"]), (400, "invalid_model_config"))
        status, error, _ = self.request(
            "PUT", "/api/model-config", updated, {"Origin": "https://evil.invalid"}
        )
        self.assertEqual((status, error["error"]), (403, "forbidden_origin"))
        status, error, _ = self.request("PUT", "/api/other", updated)
        self.assertEqual((status, error["error"]), (404, "not_found"))

    def test_invalid_existing_model_config_cannot_be_overwritten_by_ui(self) -> None:
        self.config.write_text('[providers.invalid\napi_key="secret"', encoding="utf-8")
        status, error, _ = self.request("GET", "/api/model-config")
        self.assertEqual((status, error["error"]), (409, "model_config_invalid"))
        original = self.config.read_bytes()
        status, error, _ = self.request(
            "PUT",
            "/api/model-config",
            {
                "default_model": "test",
                "providers": [
                    {
                        "id": "local",
                        "protocol": "openai_chat_completions",
                        "base_url": "http://127.0.0.1:9191/v1",
                    }
                ],
                "models": [
                    {
                        "id": "test",
                        "provider": "local",
                        "upstream_model": "fake",
                        "image_input": False,
                    }
                ],
            },
        )
        self.assertEqual((status, error["error"]), (409, "model_config_invalid"))
        self.assertEqual(self.config.read_bytes(), original)
