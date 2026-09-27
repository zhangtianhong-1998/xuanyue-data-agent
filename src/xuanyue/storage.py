"""本机用户、项目、会话及公开运行轨迹的 SQLite 存储。

每次操作使用独立连接，供 HTTP 请求线程与 Agent 后台线程共同使用。
这里不保存框架私有检查点，也不收集模型隐藏推理。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from xuanyue.types import Event, Image, Message, Text

_LOCAL_USER_ID = "local-user"
_ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_ATTACHMENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_SIGNATURES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


class RecordNotFound(LookupError):
    """请求的项目、会话或运行不属于当前本机用户。"""


class SessionBusy(RuntimeError):
    """同一会话已经有一个尚未结束的运行。"""


class ImageInputNotSupported(ValueError):
    """当前模型不接收图片，包含已完成轮次里需要回放的图片。"""


class AttachmentInUse(RuntimeError):
    """附件已经进入会话记录，不能当作未发送的草稿删除。"""


class StoreInUse(RuntimeError):
    """另一个本机服务实例已持有数据库运行锁。"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class LocalStore:
    """按用户范围查询的持久化目录；首版只启用一个本机用户。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # 恢复时会中断旧 running 记录，所以必须先独占整个服务实例。
        self._lock_fd = os.open(f"{self.path}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            self._lock_instance()
            with self._connection() as db:
                db.executescript(
                    """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS projects_by_user
                    ON projects(user_id, created_at);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    title TEXT NOT NULL,
                    kernel TEXT NOT NULL,
                    model TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_by_project
                    ON sessions(project_id, updated_at);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    question TEXT NOT NULL,
                    answer TEXT,
                    status TEXT NOT NULL CHECK(status IN
                        ('running', 'completed', 'failed', 'incomplete', 'interrupted')),
                    kernel TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_type TEXT
                );
                CREATE INDEX IF NOT EXISTS runs_by_session
                    ON runs(session_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_session
                    ON runs(session_id) WHERE status = 'running';
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    seq INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, seq)
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    mime_type TEXT NOT NULL CHECK(mime_type IN ('image/png', 'image/jpeg')),
                    size INTEGER NOT NULL CHECK(size > 0 AND size <= 5242880),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_attachments (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    attachment_id TEXT NOT NULL REFERENCES attachments(id)
                );
                """
                )
                db.execute(
                    "INSERT OR IGNORE INTO users(id, name) VALUES (?, ?)",
                    (_LOCAL_USER_ID, "本机用户"),
                )
                # 进程退出前没有终态的运行不可冒充成功；事件保留供排查。
                db.execute(
                    "UPDATE runs SET status='interrupted', updated_at=? "
                    "WHERE status='running'",
                    (_now(),),
                )
            # SQLite 默认文件权限随 umask 变化；本机运行库强制仅当前用户可读写。
            os.chmod(self.path, 0o600)
            self._prune_unattached()
        except BaseException:
            self.close()
            raise

    def _lock_instance(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                if os.path.getsize(f"{self.path}.lock") == 0:
                    os.write(self._lock_fd, b"\0")
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
                msvcrt.locking(self._lock_fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StoreInUse("database is already open by another instance") from exc

    def close(self) -> None:
        """释放跨进程锁；服务关闭或测试模拟重启时调用。"""
        fd = getattr(self, "_lock_fd", None)
        if fd is not None:
            self._lock_fd = None
            os.close(fd)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def user(self) -> dict[str, str]:
        with self._connection() as db:
            row = db.execute(
                "SELECT id, name FROM users WHERE id=?", (_LOCAL_USER_ID,)
            ).fetchone()
            return dict(row)

    def projects(self) -> list[dict[str, str]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT id, name, created_at FROM projects WHERE user_id=? "
                "ORDER BY created_at, rowid",
                (_LOCAL_USER_ID,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_project(self, name: str) -> dict[str, str]:
        project = {"id": uuid4().hex, "name": name, "created_at": _now()}
        with self._connection() as db:
            db.execute(
                "INSERT INTO projects(id,user_id,name,created_at) VALUES (?,?,?,?)",
                (project["id"], _LOCAL_USER_ID, name, project["created_at"]),
            )
        return project

    def _project_exists(self, db: sqlite3.Connection, project_id: str) -> bool:
        return (
            db.execute(
                "SELECT 1 FROM projects WHERE id=? AND user_id=?",
                (project_id, _LOCAL_USER_ID),
            ).fetchone()
            is not None
        )

    def save_attachment(
        self, project_id: str, media_type: str, data: bytes
    ) -> dict[str, object]:
        """保存单张图片到数据库旁的私有目录；只用文件头筛选，不作完整解码。"""
        signature = (
            _IMAGE_SIGNATURES.get(media_type) if isinstance(media_type, str) else None
        )
        if signature is None:
            raise ValueError("unsupported image media type")
        if type(data) is not bytes or not 0 < len(data) <= _MAX_IMAGE_BYTES:
            raise ValueError("image data must be non-empty and at most 5 MiB")
        if not data.startswith(signature):
            raise ValueError("image content does not match its media type")

        attachment = {"id": uuid4().hex, "mime_type": media_type, "size": len(data)}
        directory = self.path.parent / "attachments"
        path = directory / f"{attachment['id']}.bin"
        opened = False
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if not self._project_exists(db, project_id):
                    raise RecordNotFound("project")
                directory.mkdir(mode=0o700, exist_ok=True)
                os.chmod(directory, 0o700)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                opened = True
                with os.fdopen(fd, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(path, 0o600)
                db.execute(
                    "INSERT INTO attachments(id,project_id,mime_type,size,created_at) "
                    "VALUES (?,?,?,?,?)",
                    (attachment["id"], project_id, media_type, len(data), _now()),
                )
        except BaseException:
            # 数据库写入或提交失败时，不能留下可被误认成已登记附件的文件。
            if opened:
                path.unlink(missing_ok=True)
            raise
        return attachment

    def _prune_unattached(self) -> None:
        """重启后丢弃未进入任何 Run 的上传，并清理中断留下的孤立文件。"""
        directory = self.path.parent / "attachments"
        with self._connection() as db:
            rows = db.execute(
                "SELECT id FROM attachments WHERE id NOT IN "
                "(SELECT attachment_id FROM run_attachments)"
            ).fetchall()
            db.executemany(
                "DELETE FROM attachments WHERE id=?",
                ((row["id"],) for row in rows),
            )
            retained = {
                row["id"] for row in db.execute("SELECT id FROM attachments").fetchall()
            }
        if directory.exists():
            for path in directory.iterdir():
                attachment_id = path.stem
                if (
                    path.suffix == ".bin"
                    and _ATTACHMENT_ID.fullmatch(attachment_id)
                    and attachment_id not in retained
                ):
                    path.unlink(missing_ok=True)

    def discard_attachment(self, project_id: str, attachment_id: str) -> None:
        """发送失败时删除未绑定图片；已进入运行的图片必须留作会话证据。"""
        if not isinstance(attachment_id, str) or not _ATTACHMENT_ID.fullmatch(
            attachment_id
        ):
            raise RecordNotFound("attachment")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT a.id FROM attachments a JOIN projects p ON p.id=a.project_id "
                "WHERE a.id=? AND a.project_id=? AND p.user_id=?",
                (attachment_id, project_id, _LOCAL_USER_ID),
            ).fetchone()
            if row is None:
                raise RecordNotFound("attachment")
            if db.execute(
                "SELECT 1 FROM run_attachments WHERE attachment_id=?",
                (attachment_id,),
            ).fetchone():
                raise AttachmentInUse("attachment is linked to a run")
            db.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
        (self.path.parent / "attachments" / f"{attachment_id}.bin").unlink(
            missing_ok=True
        )

    def attachment(
        self, project_id: str, attachment_id: str
    ) -> tuple[dict[str, object], bytes]:
        """只向所属项目返回附件内容；附件 ID 从不直接用作可控路径。"""
        if not isinstance(attachment_id, str) or not _ATTACHMENT_ID.fullmatch(
            attachment_id
        ):
            raise RecordNotFound("attachment")
        with self._connection() as db:
            row = db.execute(
                "SELECT a.id,a.mime_type,a.size FROM attachments a "
                "JOIN projects p ON p.id=a.project_id "
                "WHERE a.id=? AND a.project_id=? AND p.user_id=?",
                (attachment_id, project_id, _LOCAL_USER_ID),
            ).fetchone()
            if row is None:
                raise RecordNotFound("attachment")
            metadata = dict(row)
        try:
            with (self.path.parent / "attachments" / f"{attachment_id}.bin").open(
                "rb"
            ) as source:
                data = source.read(_MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise OSError("attachment content unavailable") from exc
        if len(data) != metadata["size"] or not data.startswith(
            _IMAGE_SIGNATURES[metadata["mime_type"]]
        ):
            raise OSError("attachment content is damaged")
        return metadata, data

    def sessions(self, project_id: str) -> list[dict[str, object]]:
        with self._connection() as db:
            if not self._project_exists(db, project_id):
                raise RecordNotFound("project")
            rows = db.execute(
                "SELECT id,project_id,title,kernel,model,created_at,updated_at "
                "FROM sessions WHERE project_id=? ORDER BY updated_at DESC, rowid DESC",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_session(
        self, project_id: str, title: str, kernel: str, model: str | None
    ) -> dict[str, object]:
        at = _now()
        session = {
            "id": uuid4().hex,
            "project_id": project_id,
            "title": title,
            "kernel": kernel,
            "model": model,
            "created_at": at,
            "updated_at": at,
        }
        with self._connection() as db:
            if not self._project_exists(db, project_id):
                raise RecordNotFound("project")
            db.execute(
                "INSERT INTO sessions(id,project_id,title,kernel,model,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                tuple(session.values()),
            )
        return session

    def session(self, session_id: str) -> dict[str, object]:
        with self._connection() as db:
            row = db.execute(
                "SELECT s.id,s.project_id,s.title,s.kernel,s.model,s.created_at,s.updated_at "
                "FROM sessions s JOIN projects p ON p.id=s.project_id "
                "WHERE s.id=? AND p.user_id=?",
                (session_id, _LOCAL_USER_ID),
            ).fetchone()
            if row is None:
                raise RecordNotFound("session")
            return dict(row)

    def start_run(
        self,
        session_id: str,
        question: str,
        model: str,
        attachment_ids: tuple[str, ...] = (),
        *,
        allow_images: bool = True,
    ) -> dict[str, object]:
        """原子预留运行，防止两个请求同时向同一会话写入不一致历史。"""
        if not isinstance(attachment_ids, tuple) or len(attachment_ids) > 1:
            raise ValueError("this run supports at most one attachment")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute(
                "SELECT s.id,s.project_id,s.kernel,s.model FROM sessions s "
                "JOIN projects p ON p.id=s.project_id "
                "WHERE s.id=? AND p.user_id=?",
                (session_id, _LOCAL_USER_ID),
            ).fetchone()
            if session is None:
                raise RecordNotFound("session")
            if session["model"] not in (None, model):
                raise ValueError("session model differs from configured model")
            if not allow_images:
                # 这项检查必须与 Run 预留处在同一写事务：否则上一轮图片任务
                # 恰好完成时，文字新轮次可能绕过能力检查并回放该图片。
                prior_image = db.execute(
                    "SELECT 1 FROM runs r JOIN run_attachments ra ON ra.run_id=r.id "
                    "WHERE r.session_id=? AND r.status='completed' LIMIT 1",
                    (session_id,),
                ).fetchone()
                if attachment_ids or prior_image is not None:
                    raise ImageInputNotSupported(
                        "selected model cannot accept current or historical images"
                    )
            for attachment_id in attachment_ids:
                if not isinstance(attachment_id, str) or not _ATTACHMENT_ID.fullmatch(
                    attachment_id
                ):
                    raise RecordNotFound("attachment")
                owned = db.execute(
                    "SELECT 1 FROM attachments WHERE id=? AND project_id=?",
                    (attachment_id, session["project_id"]),
                ).fetchone()
                if owned is None:
                    raise RecordNotFound("attachment")
            at = _now()
            run = {
                "id": uuid4().hex,
                "session_id": session_id,
                "question": question,
                "answer": None,
                "status": "running",
                "kernel": session["kernel"],
                "model": model,
                "created_at": at,
                "updated_at": at,
                "error_type": None,
            }
            try:
                db.execute(
                    "INSERT INTO runs(id,session_id,question,answer,status,kernel,model,"
                    "created_at,updated_at,error_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    tuple(run.values()),
                )
            except sqlite3.IntegrityError as exc:
                raise SessionBusy("session already has an active run") from exc
            for attachment_id in attachment_ids:
                db.execute(
                    "INSERT INTO run_attachments(run_id,attachment_id) VALUES (?,?)",
                    (run["id"], attachment_id),
                )
            db.execute(
                "UPDATE sessions SET model=COALESCE(model,?), updated_at=? WHERE id=?",
                (model, at, session_id),
            )
        return run

    def images_for_run(self, run_id: str) -> tuple[Image, ...]:
        """运行前向内核装载图片，先确认运行归本机用户及附件归其项目。"""
        with self._connection() as db:
            run = db.execute(
                "SELECT s.project_id FROM runs r "
                "JOIN sessions s ON s.id=r.session_id "
                "JOIN projects p ON p.id=s.project_id "
                "WHERE r.id=? AND p.user_id=?",
                (run_id, _LOCAL_USER_ID),
            ).fetchone()
            if run is None:
                raise RecordNotFound("run")
            rows = db.execute(
                "SELECT attachment_id FROM run_attachments WHERE run_id=?",
                (run_id,),
            ).fetchall()
            project_id = run["project_id"]
        return tuple(
            Image(metadata["mime_type"], data)
            for row in rows
            for metadata, data in (self.attachment(project_id, row["attachment_id"]),)
        )

    def history(self, session_id: str, before_run_id: str) -> tuple[Message, ...]:
        """只回放本次运行之前、完整结束的公开问答及所附图片。"""
        with self._connection() as db:
            current = db.execute(
                "SELECT rowid FROM runs WHERE id=? AND session_id=?",
                (before_run_id, session_id),
            ).fetchone()
            if current is None:
                raise RecordNotFound("run")
            rows = db.execute(
                "SELECT id,question,answer FROM runs WHERE session_id=? "
                "AND rowid<? AND status='completed' AND answer IS NOT NULL "
                "AND TRIM(answer)<>'' ORDER BY rowid",
                (session_id, current["rowid"]),
            ).fetchall()
        return tuple(
            item
            for row in rows
            for item in (
                Message(
                    "user", (Text(row["question"]), *self.images_for_run(row["id"]))
                ),
                Message("assistant", (Text(row["answer"]),)),
            )
        )

    def append_event(self, run_id: str, event: Event) -> None:
        if event.run_id != run_id:
            raise ValueError("event belongs to another run")
        payload = json.dumps(dict(event.payload), ensure_ascii=False, allow_nan=False)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise ValueError("run is not active")
            expected = db.execute(
                "SELECT COALESCE(MAX(seq), 0)+1 FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if type(event.seq) is not int or event.seq != expected:
                raise ValueError("event sequence is not contiguous")
            db.execute(
                "INSERT INTO events(run_id,seq,kind,payload_json,created_at) "
                "VALUES (?,?,?,?,?)",
                (run_id, event.seq, event.kind, payload, _now()),
            )

    def finish_run(
        self, run_id: str, status: str, answer: str | None, error_type: str | None
    ) -> None:
        if status not in {"completed", "incomplete", "interrupted"}:
            raise ValueError("invalid terminal status")
        if status == "completed" and (not answer or not answer.strip()):
            raise ValueError("completed run needs a non-empty answer")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE runs SET status=?,answer=?,error_type=?,updated_at=? "
                "WHERE id=? AND status='running'",
                (
                    status,
                    answer if status == "completed" else None,
                    error_type,
                    _now(),
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("run is not active")
            db.execute(
                "UPDATE sessions SET updated_at=? WHERE id=(SELECT session_id FROM runs WHERE id=?)",
                (_now(), run_id),
            )

    def fail_run(self, run_id: str, error_type: str) -> None:
        """原子追加公开失败终态并结束 Run；只记录异常类别，不写异常正文。"""
        safe_type = (
            error_type
            if isinstance(error_type, str) and _ERROR_TYPE.fullmatch(error_type)
            else "UnknownError"
        )
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "running":
                raise ValueError("run is not active")
            next_seq = db.execute(
                "SELECT COALESCE(MAX(seq), 0)+1 FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            at = _now()
            db.execute(
                "INSERT INTO events(run_id,seq,kind,payload_json,created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    run_id,
                    next_seq,
                    "run_failed",
                    json.dumps({"error_type": safe_type}),
                    at,
                ),
            )
            db.execute(
                "UPDATE runs SET status='failed',answer=NULL,error_type=?,updated_at=? "
                "WHERE id=?",
                (safe_type, at, run_id),
            )
            db.execute(
                "UPDATE sessions SET updated_at=? "
                "WHERE id=(SELECT session_id FROM runs WHERE id=?)",
                (at, run_id),
            )

    def run(self, run_id: str) -> dict[str, object]:
        with self._connection() as db:
            row = db.execute(
                "SELECT r.id,r.session_id,r.question,r.answer,r.status,r.kernel,r.model,"
                "r.created_at,r.updated_at,r.error_type FROM runs r "
                "JOIN sessions s ON s.id=r.session_id "
                "JOIN projects p ON p.id=s.project_id "
                "WHERE r.id=? AND p.user_id=?",
                (run_id, _LOCAL_USER_ID),
            ).fetchone()
            if row is None:
                raise RecordNotFound("run")
            result = dict(row)
            rows = db.execute(
                "SELECT seq,kind,payload_json,created_at FROM events "
                "WHERE run_id=? ORDER BY seq",
                (run_id,),
            ).fetchall()
            attachments = db.execute(
                "SELECT a.id,a.mime_type,a.size FROM run_attachments ra "
                "JOIN attachments a ON a.id=ra.attachment_id "
                "JOIN runs r ON r.id=ra.run_id "
                "JOIN sessions s ON s.id=r.session_id "
                "WHERE ra.run_id=? AND a.project_id=s.project_id",
                (run_id,),
            ).fetchall()
            result["attachments"] = [dict(item) for item in attachments]
            result["events"] = [
                {
                    "seq": item["seq"],
                    "kind": item["kind"],
                    "payload": json.loads(item["payload_json"]),
                    "created_at": item["created_at"],
                }
                for item in rows
            ]
            return result

    def session_detail(self, session_id: str) -> dict[str, object]:
        session = self.session(session_id)
        with self._connection() as db:
            ids = db.execute(
                "SELECT id FROM runs WHERE session_id=? ORDER BY rowid",
                (session_id,),
            ).fetchall()
        return {"session": session, "runs": [self.run(row["id"]) for row in ids]}
