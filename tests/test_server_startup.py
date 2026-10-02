"""命令行启动边界：占用状态要给出可执行提示并释放已取得的资源。"""

from __future__ import annotations

import errno
import io
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from xuanyue.server import main
from xuanyue.storage import LocalStore


class ServerStartupTests(unittest.TestCase):
    @staticmethod
    def run_app(*arguments: str) -> tuple[int, str]:
        errors = io.StringIO()
        with (
            patch.object(sys, "argv", ["xuanyue-app", *arguments]),
            redirect_stderr(errors),
        ):
            result = main()
        return result, errors.getvalue()

    def test_existing_instance_has_actionable_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "state.sqlite3"
            existing = LocalStore(db)
            try:
                result, error = self.run_app("--db", str(db))
            finally:
                existing.close()

            self.assertEqual(result, 1)
            self.assertIn("同一数据库", error)
            self.assertIn("--db", error)
            self.assertNotIn("Traceback", error)
            self.assertNotIn(str(db), error)

    def test_busy_http_port_releases_database_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "state.sqlite3"
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                port = occupied.getsockname()[1]
                result, error = self.run_app("--db", str(db), "--port", str(port))

            self.assertEqual(result, 1)
            self.assertIn(f"127.0.0.1:{port} 端口已被占用", error)
            self.assertNotIn("Traceback", error)
            self.assertNotIn(str(db), error)
            # 第二次打开同一数据库会失败，除非启动失败时关掉了锁文件描述符。
            reopened = LocalStore(db)
            reopened.close()

    def test_other_bind_error_does_not_echo_os_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "state.sqlite3"
            with patch(
                "xuanyue.server.ThreadingHTTPServer",
                side_effect=OSError(errno.EACCES, "private socket detail"),
            ):
                result, error = self.run_app("--db", str(db))

            self.assertEqual(result, 1)
            self.assertIn("本机端口绑定失败", error)
            self.assertNotIn("private socket detail", error)
            reopened = LocalStore(db)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
