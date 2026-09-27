"""本机持久化验收：用户范围、完整历史、事件顺序和异常恢复。"""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path

from xuanyue.storage import LocalStore, RecordNotFound, SessionBusy, StoreInUse
from xuanyue.types import Event, Image, Message, Text

_PNG = b"\x89PNG\r\n\x1a\nPUBLIC-IMAGE-SENTINEL"
_JPEG = b"\xff\xd8\xffPUBLIC-JPEG-SENTINEL"


class LocalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "runtime" / "state.sqlite3"
        self.store = LocalStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_persists_hierarchy_events_and_only_completed_text_history(self) -> None:
        first_project = self.store.create_project("经营分析")
        other_project = self.store.create_project("研究")
        session = self.store.create_session(
            first_project["id"], "订单", "agentscope", "test-model"
        )
        other_session = self.store.create_session(
            other_project["id"], "报告", "langgraph", "test-model"
        )
        first = self.store.start_run(session["id"], "第一问", "test-model")
        self.store.append_event(
            first["id"], Event(first["id"], 1, "text_delta", {"delta": "答"})
        )
        self.store.append_event(
            first["id"], Event(first["id"], 2, "text_delta", {"delta": "一"})
        )
        self.store.finish_run(first["id"], "completed", "答一", None)
        failed = self.store.start_run(session["id"], "失败问", "test-model")
        self.store.append_event(
            failed["id"], Event(failed["id"], 1, "reply_started", {})
        )
        self.store.fail_run(failed["id"], "RuntimeError")
        current = self.store.start_run(session["id"], "下一问", "test-model")

        self.assertEqual(
            self.store.history(session["id"], current["id"]),
            (
                Message("user", (Text("第一问"),)),
                Message("assistant", (Text("答一"),)),
            ),
        )
        self.assertEqual(
            self.store.history(
                other_session["id"],
                self.store.start_run(other_session["id"], "独立", "test-model")["id"],
            ),
            (),
        )
        with self.assertRaises(RecordNotFound):
            self.store.sessions("unknown-project")
        with self.assertRaises(RecordNotFound):
            self.store.session("unknown-session")
        with self.assertRaises(SessionBusy):
            self.store.start_run(session["id"], "并发问", "test-model")
        with self.assertRaises(ValueError):
            self.store.append_event(
                current["id"], Event(current["id"], 2, "text_delta", {})
            )

        detail = self.store.session_detail(session["id"])
        self.assertEqual(
            [run["status"] for run in detail["runs"]],
            ["completed", "failed", "running"],
        )
        self.assertEqual(
            [event["seq"] for event in detail["runs"][0]["events"]], [1, 2]
        )
        self.assertEqual(detail["runs"][1]["answer"], None)

        with self.assertRaises(StoreInUse):
            LocalStore(self.db_path)
        self.assertEqual(self.store.run(current["id"])["status"], "running")
        self.store.close()
        self.store = LocalStore(self.db_path)
        self.assertEqual(self.store.run(current["id"])["status"], "interrupted")
        self.assertEqual(
            [project["name"] for project in self.store.projects()], ["经营分析", "研究"]
        )
        self.assertEqual(
            self.store.run(first["id"])["events"][0]["payload"], {"delta": "答"}
        )
        if os.name != "nt":
            self.assertEqual(self.db_path.stat().st_mode & 0o777, 0o600)

    def test_invalid_completion_does_not_make_a_replayable_turn(self) -> None:
        project = self.store.create_project("项目")
        session = self.store.create_session(project["id"], "会话", "langgraph", None)
        run = self.store.start_run(session["id"], "问", "model")
        with self.assertRaises(ValueError):
            self.store.finish_run(run["id"], "completed", " ", None)
        self.store.finish_run(run["id"], "incomplete", None, None)
        next_run = self.store.start_run(session["id"], "下一问", "model")
        self.assertEqual(self.store.history(session["id"], next_run["id"]), ())

    def test_image_roundtrip_and_only_completed_turn_replays_it(self) -> None:
        project = self.store.create_project("图片分析")
        session = self.store.create_session(
            project["id"], "会话", "agentscope", "model"
        )
        image = self.store.save_attachment(project["id"], "image/png", _PNG)
        self.assertEqual(
            self.store.attachment(project["id"], image["id"]), (image, _PNG)
        )

        first = self.store.start_run(session["id"], "看一下图", "model", (image["id"],))
        self.assertEqual(
            self.store.images_for_run(first["id"]), (Image("image/png", _PNG),)
        )
        self.assertEqual(self.store.run(first["id"])["attachments"], [image])
        self.store.append_event(
            first["id"], Event(first["id"], 1, "attachment_added", image)
        )
        self.store.finish_run(first["id"], "completed", "这是一张图", None)

        failed_image = self.store.save_attachment(project["id"], "image/jpeg", _JPEG)
        failed = self.store.start_run(
            session["id"], "失败的图片轮次", "model", (failed_image["id"],)
        )
        self.store.fail_run(failed["id"], "RuntimeError")
        current = self.store.start_run(session["id"], "继续", "model")
        self.assertEqual(
            self.store.history(session["id"], current["id"]),
            (
                Message("user", (Text("看一下图"), Image("image/png", _PNG))),
                Message("assistant", (Text("这是一张图"),)),
            ),
        )
        self.assertEqual(self.store.run(current["id"])["attachments"], [])

        # 图片内容只在私有附件文件中；数据库和轨迹只保存不含 base64 的元数据。
        database = self.db_path.read_bytes()
        self.assertNotIn(_PNG, database)
        self.assertNotIn(base64.b64encode(_PNG), database)
        self.assertNotIn(_JPEG, database)
        self.assertNotIn(base64.b64encode(_JPEG), database)
        if os.name != "nt":
            attachment_path = self.db_path.parent / "attachments" / f"{image['id']}.bin"
            self.assertEqual(attachment_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(attachment_path.parent.stat().st_mode & 0o777, 0o700)
        self.store.close()
        self.store = LocalStore(self.db_path)
        self.assertEqual(
            self.store.attachment(project["id"], image["id"]), (image, _PNG)
        )
        self.assertEqual(
            self.store.images_for_run(first["id"]), (Image("image/png", _PNG),)
        )

    def test_attachment_validation_and_project_isolation_are_atomic(self) -> None:
        project = self.store.create_project("本项目")
        other = self.store.create_project("另一项目")
        session = self.store.create_session(project["id"], "会话", "langgraph", "model")
        with self.assertRaises(RecordNotFound):
            self.store.save_attachment("unknown-project", "image/png", _PNG)
        for media_type, data in (
            ("image/gif", _PNG),
            ("image/png", b""),
            ("image/png", b"not a png"),
            ("image/jpeg", _PNG),
            ("image/png", b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024)),
            ("image/png", bytearray(_PNG)),
        ):
            with (
                self.subTest(media_type=media_type, length=len(data)),
                self.assertRaises(ValueError),
            ):
                self.store.save_attachment(project["id"], media_type, data)
        foreign = self.store.save_attachment(other["id"], "image/jpeg", _JPEG)
        with self.assertRaises(RecordNotFound):
            self.store.attachment(project["id"], foreign["id"])
        with self.assertRaises(RecordNotFound):
            self.store.attachment(project["id"], "../escape")
        with self.assertRaises(RecordNotFound):
            self.store.start_run(session["id"], "跨项目", "model", (foreign["id"],))
        with self.assertRaises(ValueError):
            self.store.start_run(session["id"], "两张图", "model", ("one", "two"))
        self.assertEqual(self.store.session_detail(session["id"])["runs"], [])

    def test_restart_prunes_unbound_upload_but_keeps_run_attachment(self) -> None:
        project = self.store.create_project("项目")
        session = self.store.create_session(
            project["id"], "会话", "agentscope", "model"
        )
        orphan = self.store.save_attachment(project["id"], "image/png", _PNG)
        retained = self.store.save_attachment(project["id"], "image/jpeg", _JPEG)
        run = self.store.start_run(session["id"], "看图", "model", (retained["id"],))
        self.store.finish_run(run["id"], "completed", "答复", None)
        orphan_path = self.db_path.parent / "attachments" / f"{orphan['id']}.bin"
        self.assertTrue(orphan_path.exists())

        self.store.close()
        self.store = LocalStore(self.db_path)

        with self.assertRaises(RecordNotFound):
            self.store.attachment(project["id"], orphan["id"])
        self.assertFalse(orphan_path.exists())
        self.assertEqual(
            self.store.attachment(project["id"], retained["id"]),
            (retained, _JPEG),
        )
