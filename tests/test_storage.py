"""本机持久化验收：用户范围、完整历史、事件顺序和异常恢复。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from xuanyue.storage import LocalStore, RecordNotFound, SessionBusy, StoreInUse
from xuanyue.types import Event, Message, Text


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
