"""显式合成模式可离线测试；管道输入和单次远端调用需显式授权。"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest


class CliTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "xuanyue.cli", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_both_engines_run_synthetic_task_without_configuration(self) -> None:
        for engine in ("agentscope", "langgraph"):
            with self.subTest(engine=engine):
                result = self._run(
                    "--kernel",
                    engine,
                    "--mode",
                    "synthetic",
                    "--config",
                    "does-not-exist.toml",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads(result.stdout)
                self.assertEqual(summary["kernel"], engine)
                self.assertEqual(summary["answer"], "42")
                self.assertEqual(summary["status"], "completed")

    def test_live_mode_needs_authorization_before_reading_config(self) -> None:
        result = self._run(
            "--kernel",
            "langgraph",
            "--mode",
            "live",
            "--config",
            "does-not-exist.toml",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error_type"], "ValueError")
        self.assertNotIn("does-not-exist.toml", result.stderr)

    def test_piped_chat_needs_explicit_remote_authorization(self) -> None:
        result = self._run("--kernel", "agentscope", "--config", "missing.toml")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error_type"], "ValueError")
        self.assertNotIn("missing.toml", result.stderr)
