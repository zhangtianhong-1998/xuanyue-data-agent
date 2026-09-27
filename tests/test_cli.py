"""CLI 默认可离线测试；远端调用在读取配置前必须显式授权。"""

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
                    "--kernel", engine, "--config", "does-not-exist.toml"
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
