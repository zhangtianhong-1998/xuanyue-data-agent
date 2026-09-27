"""验收摘要不能把部分事件或错误答案报告成真实模型测试通过。"""

from __future__ import annotations

import unittest

from scripts.live_agent_smoke import _summarize
from xuanyue import Event


def _events(answer: str, finish_reason: str = "completed") -> list[Event]:
    return [
        Event("test", 1, "model_call_finished", {}),
        Event("test", 2, "tool_result_finished", {"state": "success"}),
        Event("test", 3, "model_call_finished", {}),
        Event("test", 4, "text_delta", {"delta": answer}),
        Event("test", 5, "reply_finished", {"finished_reason": finish_reason}),
    ]


class LiveSmokeSummaryTests(unittest.TestCase):
    def test_only_exact_answer_and_completed_run_pass(self) -> None:
        calls = [{"orders": 21, "units_per_order": 2}]
        self.assertTrue(_summarize(_events("42"), calls)["ok"])
        self.assertFalse(_summarize(_events("420"), calls)["ok"])
        self.assertFalse(_summarize(_events("not 42, but 84"), calls)["ok"])
        self.assertFalse(_summarize(_events("42", "exceed_max_iters"), calls)["ok"])
        self.assertFalse(_summarize(_events("42"), [*calls, *calls])["ok"])
