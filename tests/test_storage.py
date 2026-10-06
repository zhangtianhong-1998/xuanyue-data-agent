"""本机持久化验收：用户范围、完整历史、事件顺序和异常恢复。"""

from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from xuanyue.storage import (
    AttachmentCleanupPending,
    LocalStore,
    RecordNotFound,
    SessionBusy,
    StoreInUse,
)
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

    def test_partial_reasoning_migration_adds_missing_snapshot_column(self) -> None:
        """旧库可能只增加过选择字段；重开仍须补齐快照列并保留已有内容。"""
        project = self.store.create_project("迁移恢复")
        session = self.store.create_session(
            project["id"], "旧会话", "agentscope", "model", reasoning="high"
        )
        run = self.store.start_run(session["id"], "旧问题", "model", reasoning="high")
        self.store.finish_run(run["id"], "completed", "旧答复", None)
        self.store.close()
        with sqlite3.connect(self.db_path) as db:
            db.execute("ALTER TABLE runs DROP COLUMN reasoning_config")
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            self.assertIn("reasoning", columns)
            self.assertNotIn("reasoning_config", columns)

        self.store = LocalStore(self.db_path)
        recovered = self.store.run(run["id"])
        self.assertEqual(recovered["reasoning"], "high")
        self.assertEqual(recovered["reasoning_config"], {})
        self.assertEqual(recovered["answer"], "旧答复")
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(self.store.session(session["id"])["reasoning"], "high")
        self.store.close()
        self.store = LocalStore(self.db_path)
        self.assertEqual(self.store.run(run["id"]), recovered)

    def test_auto_title_is_claimed_once_and_manual_rename_cancels_it(self) -> None:
        project = self.store.create_project("自动命名")
        session = self.store.create_session(
            project["id"], "新会话", "agentscope", "model", auto_title=True
        )
        first = self.store.start_run(session["id"], "失败请求", "model")
        self.store.fail_run(first["id"], "SyntheticFailure")
        second = self.store.start_run(session["id"], "成功请求", "model")
        self.store.finish_run(second["id"], "completed", "答复", None)
        self.assertTrue(self.store.claim_first_title(second["id"]))
        self.assertFalse(self.store.claim_first_title(second["id"]))
        self.assertTrue(self.store.finish_first_title(second["id"], "经营分析"))
        self.assertEqual(self.store.session(session["id"])["title"], "经营分析")
        self.assertEqual(self.store.title_state(session["id"]), "generated")
        third = self.store.start_run(session["id"], "后续请求", "model")
        self.assertFalse(self.store.claim_first_title(third["id"]))

        pending = self.store.create_session(
            project["id"], "新会话", "langgraph", "model", auto_title=True
        )
        fourth = self.store.start_run(pending["id"], "请求", "model")
        self.store.finish_run(fourth["id"], "completed", "答复", None)
        self.assertTrue(self.store.claim_first_title(fourth["id"]))
        self.store.rename_session(pending["id"], "手动标题")
        self.assertFalse(self.store.finish_first_title(fourth["id"], "模型标题"))
        self.assertEqual(self.store.session(pending["id"])["title"], "手动标题")

    def test_restart_marks_unfinished_title_generation_failed(self) -> None:
        project = self.store.create_project("恢复")
        for claimed in (False, True):
            session = self.store.create_session(
                project["id"], "新会话", "agentscope", "model", auto_title=True
            )
            run = self.store.start_run(session["id"], "问题", "model")
            self.store.finish_run(run["id"], "completed", "回答", None)
            if claimed:
                self.assertTrue(self.store.claim_first_title(run["id"]))
        self.store.close()
        self.store = LocalStore(self.db_path)
        for session in self.store.sessions(project["id"]):
            self.assertEqual(self.store.title_state(session["id"]), "failed")
            self.assertEqual(session["title"], "新会话")

    def test_rename_project_and_session_preserves_binding_and_history(self) -> None:
        project = self.store.create_project("旧项目")
        session = self.store.create_session(
            project["id"], "旧会话", "agentscope", "test-model"
        )
        first = self.store.start_run(session["id"], "原问题", "test-model")
        self.store.append_event(
            first["id"], Event(first["id"], 1, "text_delta", {"delta": "原答复"})
        )
        self.store.finish_run(first["id"], "completed", "原答复", None)

        renamed_project = self.store.rename_project(project["id"], "新项目")
        renamed_session = self.store.rename_session(session["id"], "新会话")

        self.assertEqual(
            renamed_project,
            {
                "id": project["id"],
                "name": "新项目",
                "workspace_path": None,
                "created_at": project["created_at"],
            },
        )
        self.assertEqual(
            {
                key: renamed_session[key]
                for key in ("id", "project_id", "kernel", "model")
            },
            {
                "id": session["id"],
                "project_id": project["id"],
                "kernel": "agentscope",
                "model": "test-model",
            },
        )
        self.assertEqual(renamed_session["title"], "新会话")
        self.assertEqual(self.store.projects(), [renamed_project])
        self.assertEqual(self.store.sessions(project["id"]), [renamed_session])
        self.assertEqual(
            self.store.run(first["id"])["events"][0]["payload"], {"delta": "原答复"}
        )
        following = self.store.start_run(session["id"], "下一问", "test-model")
        self.assertEqual(
            self.store.history(session["id"], following["id"]),
            (
                Message("user", (Text("原问题"),)),
                Message("assistant", (Text("原答复"),)),
            ),
        )

    def test_rename_does_not_expose_unknown_or_other_user_records(self) -> None:
        project = self.store.create_project("本机项目")
        session = self.store.create_session(
            project["id"], "本机会话", "langgraph", None
        )
        # 测试库额外注入另一个用户；产品界面仍只提供一个本机用户。
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
                    "agentscope",
                    None,
                    session["created_at"],
                    session["updated_at"],
                ),
            )

        for project_id in ("unknown-project", "other-project"):
            with self.subTest(project_id=project_id), self.assertRaises(RecordNotFound):
                self.store.rename_project(project_id, "不可改名")
        for session_id in ("unknown-session", "other-session"):
            with self.subTest(session_id=session_id), self.assertRaises(RecordNotFound):
                self.store.rename_session(session_id, "不可改名")
        self.assertEqual(self.store.projects(), [project])
        self.assertEqual(self.store.session(session["id"]), session)
        with self.store._connection() as db:
            self.assertEqual(
                db.execute(
                    "SELECT name FROM projects WHERE id='other-project'"
                ).fetchone()[0],
                "私有项目",
            )
            self.assertEqual(
                db.execute(
                    "SELECT title FROM sessions WHERE id='other-session'"
                ).fetchone()[0],
                "私有会话",
            )

    def test_delete_session_and_project_clean_dependent_rows_and_images(self) -> None:
        project = self.store.create_project("待删项目")
        first = self.store.create_session(project["id"], "待删会话", "agentscope", "m")
        retained = self.store.create_session(
            project["id"], "保留会话", "langgraph", "m"
        )
        other = self.store.create_project("另一个项目")
        other_session = self.store.create_session(
            other["id"], "其他会话", "langgraph", "m"
        )
        shared = self.store.save_attachment(project["id"], "image/png", _PNG)
        exclusive = self.store.save_attachment(project["id"], "image/jpeg", _JPEG)
        draft = self.store.save_attachment(project["id"], "image/png", _PNG)
        other_image = self.store.save_attachment(other["id"], "image/png", _PNG)

        for session, image in (
            (first, shared),
            (first, exclusive),
            (retained, shared),
            (other_session, other_image),
        ):
            run = self.store.start_run(session["id"], "问题", "m", (image["id"],))
            self.store.append_event(
                run["id"], Event(run["id"], 1, "text_delta", {"delta": "答"})
            )
            self.store.finish_run(run["id"], "completed", "答", None)

        first_runs = [
            item["id"] for item in self.store.session_detail(first["id"])["runs"]
        ]
        self.store.delete_session(first["id"])
        with self.assertRaises(RecordNotFound):
            self.store.session(first["id"])
        self.assertEqual(len(self.store.session_detail(retained["id"])["runs"]), 1)
        for run_id in first_runs:
            with self.assertRaises(RecordNotFound):
                self.store.run(run_id)
        self.assertEqual(
            self.store.attachment(project["id"], shared["id"]), (shared, _PNG)
        )
        self.assertEqual(
            self.store.attachment(project["id"], draft["id"]), (draft, _PNG)
        )
        with self.assertRaises(RecordNotFound):
            self.store.attachment(project["id"], exclusive["id"])
        self.assertFalse(
            (self.db_path.parent / "attachments" / f"{exclusive['id']}.bin").exists()
        )

        self.store.delete_project(project["id"])
        self.assertEqual(self.store.projects(), [other])
        for image in (shared, draft):
            self.assertFalse(
                (self.db_path.parent / "attachments" / f"{image['id']}.bin").exists()
            )
        self.assertEqual(
            self.store.attachment(other["id"], other_image["id"]),
            (other_image, _PNG),
        )
        with self.store._connection() as db:
            for table in (
                "projects",
                "sessions",
                "runs",
                "events",
                "attachments",
                "run_attachments",
            ):
                self.assertEqual(
                    db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1
                )

    def test_delete_rejects_running_unknown_and_foreign_records(self) -> None:
        project = self.store.create_project("本机项目")
        session = self.store.create_session(project["id"], "进行中", "agentscope", "m")
        image = self.store.save_attachment(project["id"], "image/png", _PNG)
        active = self.store.start_run(session["id"], "未完成", "m", (image["id"],))
        for operation in (
            lambda: self.store.delete_session(session["id"]),
            lambda: self.store.delete_project(project["id"]),
        ):
            with self.assertRaises(SessionBusy):
                operation()
        self.assertEqual(self.store.run(active["id"])["status"], "running")
        self.assertEqual(
            self.store.attachment(project["id"], image["id"]), (image, _PNG)
        )

        with self.store._connection() as db:
            db.execute("INSERT INTO users(id,name) VALUES (?,?)", ("other", "其他用户"))
            db.execute(
                "INSERT INTO projects(id,user_id,name,created_at) VALUES (?,?,?,?)",
                ("foreign-project", "other", "不可删", project["created_at"]),
            )
            db.execute(
                "INSERT INTO sessions(id,project_id,title,kernel,model,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    "foreign-session",
                    "foreign-project",
                    "不可删",
                    "langgraph",
                    None,
                    session["created_at"],
                    session["updated_at"],
                ),
            )
        for project_id in ("missing", "foreign-project"):
            with self.assertRaises(RecordNotFound):
                self.store.delete_project(project_id)
        for session_id in ("missing", "foreign-session"):
            with self.assertRaises(RecordNotFound):
                self.store.delete_session(session_id)

        self.store.finish_run(active["id"], "completed", "完成", None)
        self.store.delete_project(project["id"])
        with self.store._connection() as db:
            self.assertEqual(
                db.execute(
                    "SELECT title FROM sessions WHERE id='foreign-session'"
                ).fetchone()[0],
                "不可删",
            )

    def test_project_workspace_path_is_normalized_and_old_db_migrates(self) -> None:
        selected = self.db_path.parent / ".."
        project = self.store.create_project("新项目", str(selected))
        self.assertEqual(
            project["workspace_path"], str(self.db_path.parent.parent.resolve())
        )
        self.assertEqual(self.store.projects(), [project])
        self.assertEqual(
            self.store.rename_project(project["id"], "改名")["workspace_path"],
            str(self.db_path.parent.parent.resolve()),
        )
        for invalid in (
            "relative",
            str(self.db_path),
            str(self.db_path.parent / "missing"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.create_project("无效", invalid)

        legacy_path = self.db_path.parent / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as db:
            db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE projects (id TEXT PRIMARY KEY, "
                "user_id TEXT NOT NULL REFERENCES users(id), "
                "name TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, "
                "project_id TEXT NOT NULL REFERENCES projects(id), "
                "title TEXT NOT NULL, kernel TEXT NOT NULL, model TEXT, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE runs (id TEXT PRIMARY KEY, "
                "session_id TEXT NOT NULL REFERENCES sessions(id), "
                "question TEXT NOT NULL, answer TEXT, status TEXT NOT NULL, "
                "kernel TEXT NOT NULL, model TEXT NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error_type TEXT)"
            )
            db.execute("INSERT INTO users(id,name) VALUES ('local-user','本机')")
            db.execute(
                "INSERT INTO projects(id,user_id,name,created_at) "
                "VALUES ('legacy','local-user','旧项目','2020-01-01')"
            )
            db.execute(
                "INSERT INTO sessions(id,project_id,title,kernel,model,created_at,updated_at) "
                "VALUES ('legacy-session','legacy','旧标题','agentscope','old',"
                "'2020-01-01','2020-01-01')"
            )
            db.execute(
                "INSERT INTO runs(id,session_id,question,answer,status,kernel,model,created_at,updated_at) "
                "VALUES ('legacy-run','legacy-session','旧问题','旧答复','completed',"
                "'agentscope','old','2020-01-01','2020-01-01')"
            )
        legacy = LocalStore(legacy_path)
        try:
            self.assertEqual(
                legacy.projects(),
                [
                    {
                        "id": "legacy",
                        "name": "旧项目",
                        "workspace_path": None,
                        "created_at": "2020-01-01",
                    }
                ],
            )
            self.assertIsNone(
                legacy.rename_project("legacy", "保留项目")["workspace_path"]
            )
            self.assertEqual(legacy.session("legacy-session")["title"], "旧标题")
            self.assertEqual(legacy.title_state("legacy-session"), "manual")
            self.assertEqual(legacy.session("legacy-session")["reasoning"], "default")
            migrated_run = legacy.run("legacy-run")
            self.assertEqual(migrated_run["model"], "old")
            self.assertEqual(migrated_run["answer"], "旧答复")
            self.assertEqual(migrated_run["reasoning"], "default")
            self.assertEqual(migrated_run["reasoning_config"], {})
            # 再次打开不能反填当前档位，也不能丢失旧答复。
            legacy.close()
            legacy = LocalStore(legacy_path)
            self.assertEqual(legacy.run("legacy-run"), migrated_run)
        finally:
            legacy.close()

    def test_deleted_image_left_by_filesystem_failure_is_pruned_on_restart(
        self,
    ) -> None:
        project = self.store.create_project("临时项目")
        image = self.store.save_attachment(project["id"], "image/png", _PNG)
        path = self.db_path.parent / "attachments" / f"{image['id']}.bin"
        with (
            patch.object(
                Path, "unlink", side_effect=OSError("synthetic unlink failure")
            ),
            self.assertRaises(AttachmentCleanupPending),
        ):
            self.store.delete_project(project["id"])
        self.assertTrue(path.exists())
        self.assertEqual(self.store.projects(), [])
        self.store.close()
        self.store = LocalStore(self.db_path)
        self.assertFalse(path.exists())

    def test_pending_file_cleanup_retries_during_next_delete(self) -> None:
        first = self.store.create_project("第一次删除")
        image = self.store.save_attachment(first["id"], "image/png", _PNG)
        path = self.db_path.parent / "attachments" / f"{image['id']}.bin"
        with (
            patch.object(Path, "unlink", side_effect=OSError("synthetic failure")),
            self.assertRaises(AttachmentCleanupPending),
        ):
            self.store.delete_project(first["id"])
        self.assertTrue(path.exists())

        second = self.store.create_project("第二次删除")
        self.store.delete_project(second["id"])
        self.assertFalse(path.exists())

    def test_session_creation_rechecks_project_after_delete_commits(self) -> None:
        project = self.store.create_project("可并发删除")
        held = threading.Event()
        release = threading.Event()
        create_started = threading.Event()
        outcomes: list[object] = []
        original_exists = self.store._project_exists

        def pause_after_delete_lock(db: sqlite3.Connection, project_id: str) -> bool:
            if threading.current_thread().name == "delete-project":
                held.set()
                if not release.wait(timeout=3):
                    raise AssertionError("delete lock was not released")
            return original_exists(db, project_id)

        def delete() -> None:
            try:
                self.store.delete_project(project["id"])
                outcomes.append("deleted")
            except Exception as exc:  # noqa: BLE001
                outcomes.append(exc)

        def create() -> None:
            create_started.set()
            try:
                outcomes.append(
                    self.store.create_session(
                        project["id"], "迟到会话", "langgraph", None
                    )
                )
            except Exception as exc:  # noqa: BLE001
                outcomes.append(exc)

        with patch.object(
            self.store, "_project_exists", side_effect=pause_after_delete_lock
        ):
            deleting = threading.Thread(target=delete, name="delete-project")
            creating = threading.Thread(target=create, name="create-session")
            deleting.start()
            try:
                self.assertTrue(held.wait(timeout=3))
                creating.start()
                self.assertTrue(create_started.wait(timeout=3))
                self.assertFalse(any(isinstance(item, dict) for item in outcomes))
            finally:
                release.set()
                deleting.join(timeout=3)
                if creating.ident is not None:
                    creating.join(timeout=3)
        self.assertFalse(deleting.is_alive())
        self.assertFalse(creating.is_alive())
        self.assertIn("deleted", outcomes)
        self.assertTrue(any(isinstance(item, RecordNotFound) for item in outcomes))
        self.assertFalse(any(isinstance(item, dict) for item in outcomes))

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

    def test_four_images_keep_selection_order_after_restart(self) -> None:
        project = self.store.create_project("多图项目")
        session = self.store.create_session(project["id"], "会话", "langgraph", "m")
        uploaded = [
            self.store.save_attachment(project["id"], "image/png", _PNG + bytes([i]))
            for i in range(4)
        ]
        selected = [uploaded[i] for i in (3, 1, 0, 2)]
        first = self.store.start_run(
            session["id"], "按顺序看图", "m", tuple(item["id"] for item in selected)
        )
        expected_images = tuple(
            Image("image/png", _PNG + bytes([i])) for i in (3, 1, 0, 2)
        )
        self.assertEqual(self.store.images_for_run(first["id"]), expected_images)
        self.assertEqual(self.store.run(first["id"])["attachments"], selected)
        self.store.finish_run(first["id"], "completed", "已看图", None)

        self.store.close()
        self.store = LocalStore(self.db_path)
        self.assertEqual(self.store.run(first["id"])["attachments"], selected)
        later = self.store.start_run(session["id"], "记得顺序吗", "m")
        self.assertEqual(
            self.store.history(session["id"], later["id"]),
            (
                Message("user", (Text("按顺序看图"), *expected_images)),
                Message("assistant", (Text("已看图"),)),
            ),
        )
        with self.assertRaises(ValueError):
            self.store.start_run(
                session["id"], "重复图片", "m", (uploaded[0]["id"],) * 2
            )
        with self.assertRaises(ValueError):
            self.store.start_run(
                session["id"],
                "超量图片",
                "m",
                tuple(item["id"] for item in uploaded) + (uploaded[0]["id"],),
            )

    def test_old_single_image_table_migrates_without_losing_history(self) -> None:
        project = self.store.create_project("旧库")
        session = self.store.create_session(project["id"], "会话", "agentscope", "m")
        image = self.store.save_attachment(project["id"], "image/png", _PNG)
        first = self.store.start_run(session["id"], "旧图片", "m", (image["id"],))
        self.store.finish_run(first["id"], "completed", "旧答复", None)
        self.store.close()
        with sqlite3.connect(self.db_path) as db:
            db.execute("DROP TABLE run_attachments")
            db.execute(
                "CREATE TABLE run_attachments ("
                "run_id TEXT PRIMARY KEY REFERENCES runs(id), "
                "attachment_id TEXT NOT NULL REFERENCES attachments(id))"
            )
            db.execute(
                "INSERT INTO run_attachments(run_id,attachment_id) VALUES (?,?)",
                (first["id"], image["id"]),
            )

        self.store = LocalStore(self.db_path)
        self.assertEqual(self.store.run(first["id"])["attachments"], [image])
        with self.store._connection() as db:
            self.assertEqual(
                db.execute(
                    "SELECT ordinal FROM run_attachments WHERE run_id=?", (first["id"],)
                ).fetchone()[0],
                0,
            )
        later = self.store.start_run(session["id"], "继续", "m")
        self.assertEqual(
            self.store.history(session["id"], later["id"])[0].parts,
            (Text("旧图片"), Image("image/png", _PNG)),
        )
        self.store.finish_run(later["id"], "completed", "继续", None)
        second_image = self.store.save_attachment(project["id"], "image/jpeg", _JPEG)
        new_run = self.store.start_run(
            session["id"], "新多图", "m", (second_image["id"], image["id"])
        )
        self.assertEqual(
            self.store.images_for_run(new_run["id"]),
            (Image("image/jpeg", _JPEG), Image("image/png", _PNG)),
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
        with self.assertRaises(RecordNotFound):
            self.store.start_run(session["id"], "无效图片", "model", ("one", "two"))
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
