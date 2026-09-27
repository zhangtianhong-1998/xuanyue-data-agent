"""本机用户、项目、会话及公开运行轨迹的 SQLite 存储。

每次操作使用独立连接，供 HTTP 请求线程与 Agent 后台线程共同使用。
这里不保存框架私有检查点，也不收集模型隐藏推理。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
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


class AttachmentCleanupPending(RuntimeError):
    """记录已提交删除，但至少一个附件文件尚待清理。"""


class StoreInUse(RuntimeError):
    """另一个本机服务实例已持有数据库运行锁。"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _normalize_workspace_path(value: str) -> str:
    """只核对项目目录的位置和类型；登记目录不授权 Agent 读取内容。"""
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("invalid workspace path")
    selected = Path(value)
    if not selected.is_absolute():
        raise ValueError("workspace path must be absolute")
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("workspace path is unavailable") from exc
    if not resolved.is_dir():
        raise ValueError("workspace path must be a directory")
    return str(resolved)


class LocalStore:
    """按用户范围查询的持久化目录；首版只启用一个本机用户。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cleanup_lock = threading.Lock()
        self._pending_attachment_cleanup: set[str] = set()
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
                    workspace_path TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS projects_by_user
                    ON projects(user_id, created_at);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    title TEXT NOT NULL,
                    title_state TEXT NOT NULL DEFAULT 'manual',
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
                # 旧本机库已有项目时保留原记录；目录由用户之后明确指定。
                columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(projects)")
                }
                if "workspace_path" not in columns:
                    db.execute("ALTER TABLE projects ADD COLUMN workspace_path TEXT")
                # 旧会话的标题视为用户已确定；只有新建时省略标题才会自动命名。
                session_columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(sessions)")
                }
                if "title_state" not in session_columns:
                    db.execute(
                        "ALTER TABLE sessions ADD COLUMN title_state TEXT "
                        "NOT NULL DEFAULT 'manual'"
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
                # 标题请求不属于 Run；进程重启后不能把它永远留在“生成中”。
                db.execute(
                    "UPDATE sessions SET title_state='failed' "
                    "WHERE title_state IN ('pending','generating') AND EXISTS "
                    "(SELECT 1 FROM runs WHERE runs.session_id=sessions.id "
                    "AND runs.status='completed')"
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

    def projects(self) -> list[dict[str, str | None]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT id, name, workspace_path, created_at FROM projects WHERE user_id=? "
                "ORDER BY created_at, rowid",
                (_LOCAL_USER_ID,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_project(
        self, name: str, workspace_path: str | None = None
    ) -> dict[str, str | None]:
        """创建项目；HTTP 必传目录，空值只用于兼容旧库及内部测试。"""
        normalized = (
            _normalize_workspace_path(workspace_path)
            if workspace_path is not None
            else None
        )
        project = {
            "id": uuid4().hex,
            "name": name,
            "workspace_path": normalized,
            "created_at": _now(),
        }
        with self._connection() as db:
            db.execute(
                "INSERT INTO projects(id,user_id,name,workspace_path,created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    project["id"],
                    _LOCAL_USER_ID,
                    name,
                    normalized,
                    project["created_at"],
                ),
            )
        return project

    def rename_project(self, project_id: str, name: str) -> dict[str, str | None]:
        """只修改本机用户的项目名称；项目下的会话与运行不迁移。"""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE projects SET name=? WHERE id=? AND user_id=?",
                (name, project_id, _LOCAL_USER_ID),
            ).rowcount
            if changed != 1:
                raise RecordNotFound("project")
            row = db.execute(
                "SELECT id,name,workspace_path,created_at FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
            return dict(row)

    def delete_project(self, project_id: str) -> None:
        """删除本机项目及其会话、运行和附件；进行中的运行必须先结束。"""
        with self._connection() as db:
            # 与 start_run 同用写事务，避免检查完成后又创建新的运行。
            db.execute("BEGIN IMMEDIATE")
            if not self._project_exists(db, project_id):
                raise RecordNotFound("project")
            active = db.execute(
                "SELECT 1 FROM runs r JOIN sessions s ON s.id=r.session_id "
                "WHERE s.project_id=? AND r.status='running' LIMIT 1",
                (project_id,),
            ).fetchone()
            if active is not None:
                raise SessionBusy("project has an active run")
            attachment_ids = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM attachments WHERE project_id=?", (project_id,)
                ).fetchall()
            ]
            session_ids = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM sessions WHERE project_id=?", (project_id,)
                ).fetchall()
            ]
            self._delete_run_rows(db, session_ids)
            db.execute("DELETE FROM sessions WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM attachments WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        self._remove_attachment_files(attachment_ids)

    @staticmethod
    def _delete_run_rows(db: sqlite3.Connection, session_ids: list[str]) -> None:
        """按外键依赖顺序删运行；调用方持有写事务并检查运行终态。"""
        for session_id in session_ids:
            parameters = (session_id,)
            for table in ("run_attachments", "events"):
                db.execute(
                    f"DELETE FROM {table} WHERE run_id IN "
                    "(SELECT id FROM runs WHERE session_id=?)",
                    parameters,
                )
            db.execute("DELETE FROM runs WHERE session_id=?", parameters)

    def _remove_attachment_files(self, attachment_ids: list[str]) -> None:
        """DB 提交后删文件；失败 ID 在同进程下一次删除时重试。"""
        directory = self.path.parent / "attachments"
        current = {
            attachment_id
            for attachment_id in attachment_ids
            if isinstance(attachment_id, str)
            and _ATTACHMENT_ID.fullmatch(attachment_id)
        }
        # 数据库 ID 也不直接充当文件名，以免旧库或手工数据造成越界。
        with self._cleanup_lock:
            self._pending_attachment_cleanup.update(current)
            for attachment_id in tuple(self._pending_attachment_cleanup):
                try:
                    (directory / f"{attachment_id}.bin").unlink(missing_ok=True)
                except OSError:
                    continue
                self._pending_attachment_cleanup.remove(attachment_id)
            if current & self._pending_attachment_cleanup:
                raise AttachmentCleanupPending("attachment file cleanup is pending")

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
            orphan_ids: list[str] = []
            for path in directory.iterdir():
                attachment_id = path.stem
                if (
                    path.suffix == ".bin"
                    and _ATTACHMENT_ID.fullmatch(attachment_id)
                    and attachment_id not in retained
                ):
                    orphan_ids.append(attachment_id)
            try:
                self._remove_attachment_files(orphan_ids)
            except AttachmentCleanupPending:
                # 文件系统故障不应阻止打开已恢复的本机库；下次删除会重试。
                pass

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
        self._remove_attachment_files([attachment_id])

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

    def models_in_use(self) -> frozenset[str]:
        """设置页删除模型前查询会话引用；旧会话不能被悄悄断开。"""
        with self._connection() as db:
            rows = db.execute(
                "SELECT DISTINCT s.model FROM sessions AS s "
                "JOIN projects AS p ON p.id=s.project_id "
                "WHERE p.user_id=? AND s.model IS NOT NULL",
                (_LOCAL_USER_ID,),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def create_session(
        self,
        project_id: str,
        title: str,
        kernel: str,
        model: str | None,
        *,
        auto_title: bool = False,
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
            # 与项目删除互斥：必须取得写锁后再确认父项目仍存在。
            db.execute("BEGIN IMMEDIATE")
            if not self._project_exists(db, project_id):
                raise RecordNotFound("project")
            db.execute(
                "INSERT INTO sessions(id,project_id,title,title_state,kernel,model,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    session["id"],
                    project_id,
                    title,
                    "pending" if auto_title else "manual",
                    kernel,
                    model,
                    at,
                    at,
                ),
            )
        return session

    def rename_session(self, session_id: str, title: str) -> dict[str, object]:
        """只修改标题；主内核、模型绑定和已有运行始终沿用原会话。"""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE sessions SET title=?,title_state='manual',updated_at=? "
                "WHERE id=? AND project_id IN "
                "(SELECT id FROM projects WHERE user_id=?)",
                (title, _now(), session_id, _LOCAL_USER_ID),
            ).rowcount
            if changed != 1:
                raise RecordNotFound("session")
            row = db.execute(
                "SELECT id,project_id,title,kernel,model,created_at,updated_at "
                "FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            return dict(row)

    def claim_first_title(self, run_id: str) -> bool:
        """仅首个有完整答复的运行可占用自动命名；手动改名会取消占用。"""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT r.id,r.session_id,r.status,s.title_state FROM runs r "
                "JOIN sessions s ON s.id=r.session_id "
                "JOIN projects p ON p.id=s.project_id "
                "WHERE r.id=? AND p.user_id=?",
                (run_id, _LOCAL_USER_ID),
            ).fetchone()
            if (
                row is None
                or row["status"] != "completed"
                or row["title_state"] != "pending"
            ):
                return False
            earlier = db.execute(
                "SELECT 1 FROM runs WHERE session_id=? AND rowid < "
                "(SELECT rowid FROM runs WHERE id=?) AND status='completed' LIMIT 1",
                (row["session_id"], run_id),
            ).fetchone()
            if earlier is not None:
                return False
            db.execute(
                "UPDATE sessions SET title_state='generating' WHERE id=?",
                (row["session_id"],),
            )
            return True

    def finish_first_title(self, run_id: str, title: str | None) -> bool:
        """只提交仍由自动命名占用的标题；失败与用户改名均不会被覆盖。"""
        if title is not None and (not title.strip() or len(title) > 80):
            raise ValueError("generated title is invalid")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if title is None:
                changed = db.execute(
                    "UPDATE sessions SET title_state='failed' "
                    "WHERE id=(SELECT session_id FROM runs WHERE id=?) "
                    "AND title_state='generating'",
                    (run_id,),
                ).rowcount
            else:
                changed = db.execute(
                    "UPDATE sessions SET title=?,title_state='generated',updated_at=? "
                    "WHERE id=(SELECT session_id FROM runs WHERE id=?) "
                    "AND title_state='generating'",
                    (title, _now(), run_id),
                ).rowcount
            return changed == 1

    def delete_session(self, session_id: str) -> None:
        """删除本机会话；其他会话仍引用的图片保持可读。"""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT s.project_id FROM sessions s "
                "JOIN projects p ON p.id=s.project_id "
                "WHERE s.id=? AND p.user_id=?",
                (session_id, _LOCAL_USER_ID),
            ).fetchone()
            if row is None:
                raise RecordNotFound("session")
            active = db.execute(
                "SELECT 1 FROM runs WHERE session_id=? AND status='running' LIMIT 1",
                (session_id,),
            ).fetchone()
            if active is not None:
                raise SessionBusy("session has an active run")
            candidates = [
                item["attachment_id"]
                for item in db.execute(
                    "SELECT DISTINCT ra.attachment_id FROM run_attachments ra "
                    "JOIN runs r ON r.id=ra.run_id WHERE r.session_id=?",
                    (session_id,),
                ).fetchall()
            ]
            self._delete_run_rows(db, [session_id])
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            removed: list[str] = []
            for attachment_id in candidates:
                changed = db.execute(
                    "DELETE FROM attachments WHERE id=? AND project_id=? "
                    "AND NOT EXISTS (SELECT 1 FROM run_attachments "
                    "WHERE attachment_id=?)",
                    (attachment_id, row["project_id"], attachment_id),
                ).rowcount
                if changed:
                    removed.append(attachment_id)
        self._remove_attachment_files(removed)

    def _session_snapshot(self, session_id: str) -> tuple[dict[str, object], str]:
        """同一次读取标题及其状态，避免界面漏掉刚完成的自动命名。"""
        with self._connection() as db:
            row = db.execute(
                "SELECT s.id,s.project_id,s.title,s.title_state,s.kernel,s.model,"
                "s.created_at,s.updated_at "
                "FROM sessions s JOIN projects p ON p.id=s.project_id "
                "WHERE s.id=? AND p.user_id=?",
                (session_id, _LOCAL_USER_ID),
            ).fetchone()
            if row is None:
                raise RecordNotFound("session")
            session = dict(row)
            title_state = session.pop("title_state")
            return session, title_state

    def session(self, session_id: str) -> dict[str, object]:
        return self._session_snapshot(session_id)[0]

    def title_state(self, session_id: str) -> str:
        """供会话详情区分标题尚在生成、已经生成或用户已手动命名。"""
        return self._session_snapshot(session_id)[1]

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
        session, title_state = self._session_snapshot(session_id)
        with self._connection() as db:
            ids = db.execute(
                "SELECT id FROM runs WHERE session_id=? ORDER BY rowid",
                (session_id,),
            ).fetchall()
        return {
            "session": session,
            "title_state": title_state,
            "runs": [self.run(row["id"]) for row in ids],
        }
