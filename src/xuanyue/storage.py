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

from xuanyue.types import Event, Message, Text

_LOCAL_USER_ID = "local-user"
_ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


class RecordNotFound(LookupError):
    """请求的项目、会话或运行不属于当前本机用户。"""


class SessionBusy(RuntimeError):
    """同一会话已经有一个尚未结束的运行。"""


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
        self, session_id: str, question: str, model: str
    ) -> dict[str, object]:
        """原子预留运行，防止两个请求同时向同一会话写入不一致历史。"""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute(
                "SELECT s.id,s.kernel,s.model FROM sessions s "
                "JOIN projects p ON p.id=s.project_id "
                "WHERE s.id=? AND p.user_id=?",
                (session_id, _LOCAL_USER_ID),
            ).fetchone()
            if session is None:
                raise RecordNotFound("session")
            if session["model"] not in (None, model):
                raise ValueError("session model differs from configured model")
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
            db.execute(
                "UPDATE sessions SET model=COALESCE(model,?), updated_at=? WHERE id=?",
                (model, at, session_id),
            )
        return run

    def history(self, session_id: str, before_run_id: str) -> tuple[Message, ...]:
        """只回放本次运行之前、完整结束的文字问答，不回放工具细节。"""
        with self._connection() as db:
            current = db.execute(
                "SELECT rowid FROM runs WHERE id=? AND session_id=?",
                (before_run_id, session_id),
            ).fetchone()
            if current is None:
                raise RecordNotFound("run")
            rows = db.execute(
                "SELECT question,answer FROM runs WHERE session_id=? "
                "AND rowid<? AND status='completed' AND answer IS NOT NULL "
                "AND TRIM(answer)<>'' ORDER BY rowid",
                (session_id, current["rowid"]),
            ).fetchall()
        return tuple(
            item
            for row in rows
            for item in (
                Message("user", (Text(row["question"]),)),
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
