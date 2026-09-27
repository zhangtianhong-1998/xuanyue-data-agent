"""交互 CLI 的会话历史只由已完成且有答复的轮次组成。"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

from xuanyue.cli import _chat
from xuanyue.engines import EngineRegistry
from xuanyue.types import Message, Text


class ChatCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_failed_and_empty_answer_turns_do_not_enter_history(
        self,
    ) -> None:
        inputs = [
            "第一问",
            "   ",
            "失败问",
            "空答复问",
            "第二问",
            "第三问",
            "/exit",
        ]
        responses: list[dict[str, object] | Exception] = [
            {"type": "summary", "status": "completed", "answer": "答复一"},
            RuntimeError("private-provider-error"),
            {"type": "summary", "status": "completed", "answer": "   "},
            {"type": "summary", "status": "completed", "answer": "答复二"},
            {"type": "summary", "status": "completed", "answer": "答复三"},
        ]
        seen: list[tuple[str, tuple[Message, ...]]] = []

        async def execute(*args: object, **kwargs: object) -> dict[str, object]:
            # 模拟执行边界，记录产品交给下一轮内核的完整文字历史。
            seen.append((str(args[3]), kwargs["history"]))
            outcome = responses[len(seen) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch("builtins.input", side_effect=inputs) as read_input,
            patch("xuanyue.cli._execute", side_effect=execute),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            await _chat(
                EngineRegistry(), "agentscope", "chosen", "upstream", None, False
            )

        first_turn = (
            Message("user", (Text("第一问"),)),
            Message("assistant", (Text("答复一"),)),
        )
        second_turn = (
            Message("user", (Text("第二问"),)),
            Message("assistant", (Text("答复二"),)),
        )
        self.assertEqual(read_input.call_count, len(inputs))
        self.assertEqual(
            seen,
            [
                ("第一问", ()),
                ("失败问", first_turn),
                ("空答复问", first_turn),
                ("第二问", first_turn),
                ("第三问", first_turn + second_turn),
            ],
        )
        self.assertNotIn("private-provider-error", output.getvalue())
        self.assertNotIn("private-provider-error", errors.getvalue())

    async def test_eof_exits_without_running_a_task(self) -> None:
        with (
            patch("builtins.input", side_effect=EOFError),
            patch("xuanyue.cli._execute", new_callable=AsyncMock) as execute,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            await _chat(
                EngineRegistry(), "langgraph", "chosen", "upstream", None, False
            )
        execute.assert_not_awaited()
