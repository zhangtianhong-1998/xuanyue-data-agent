"""两套真实内核应接收同一段已完成的文字对话历史。"""

from __future__ import annotations

import unittest

from xuanyue import Runtime, Task
from xuanyue.engines.agentscope import AgentScopeKernel
from xuanyue.engines.langgraph import LangGraphKernel
from xuanyue.llm import ModelRoute, ModelRouter
from xuanyue.tools import LocalTools
from xuanyue.types import Message, ModelReply, ModelRequest, Text, ToolCall


class _RecordingModel:
    """只记录内核实际提交的请求，不模拟内核的对话处理。"""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        return ModelReply((Text("第二轮答复"),))


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_engines_receive_completed_text_history_in_order(self) -> None:
        history = (
            Message("user", (Text("第一轮问题"),)),
            Message("assistant", (Text("第一轮答复"),)),
        )
        for kernel_class in (AgentScopeKernel, LangGraphKernel):
            with self.subTest(kernel=kernel_class.id):
                model = _RecordingModel()
                runtime = Runtime(
                    [
                        kernel_class(
                            ModelRouter({"chosen": ModelRoute("upstream", model)}),
                            LocalTools([]),
                            "请简短回答。",
                        )
                    ]
                )
                task = Task(
                    "second-turn",
                    kernel_class.id,
                    "chosen",
                    "第二轮问题",
                    history=history,
                )

                events = [event async for event in runtime.stream(task)]

                self.assertEqual(events[-1].kind, "reply_finished")
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(model.requests[0].model, "upstream")
                self.assertEqual(
                    [
                        (message.role, tuple(part.value for part in message.parts))
                        for message in model.requests[0].messages
                    ],
                    [
                        ("system", ("请简短回答。",)),
                        ("user", ("第一轮问题",)),
                        ("assistant", ("第一轮答复",)),
                        ("user", ("第二轮问题",)),
                    ],
                )

    def test_task_rejects_incomplete_or_non_text_history(self) -> None:
        invalid_histories = {
            "unfinished_user_turn": (Message("user", (Text("第一轮问题"),)),),
            "wrong_order": (
                Message("assistant", (Text("第一轮答复"),)),
                Message("user", (Text("第一轮问题"),)),
            ),
            "tool_call": (
                Message("user", (Text("第一轮问题"),)),
                Message("assistant", (ToolCall("call-1", "multiply", "{}"),)),
            ),
            "private_content": (
                Message("user", (Text("第一轮问题"),)),
                Message("assistant", (Text("第一轮答复"),), True),
            ),
            "system_message": (
                Message("system", (Text("旧系统提示"),)),
                Message("assistant", (Text("第一轮答复"),)),
            ),
        }
        for name, history in invalid_histories.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                Task(
                    "second-turn", "agentscope", "chosen", "第二轮问题", history=history
                )
