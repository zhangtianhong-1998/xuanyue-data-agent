"""同一产品任务通过两种真实 Agent 内核完成合成工具往返。"""

from __future__ import annotations

import json
import unittest

from xuanyue import Runtime, Task
from xuanyue.agentscope import AgentScopeKernel
from xuanyue.langgraph import LangGraphKernel, UnsupportedLangGraphContent
from xuanyue.llm import ModelRoute, ModelRouter
from xuanyue.tools import LocalTools, ReadOnlyTool
from xuanyue.types import ModelReply, ModelRequest, Text, ToolCall, ToolResult, ToolSpec

SPEC = ToolSpec(
    "multiply",
    "Multiply synthetic order counts.",
    {
        "type": "object",
        "properties": {
            "orders": {"type": "integer"},
            "units_per_order": {"type": "integer"},
        },
        "required": ["orders", "units_per_order"],
        "additionalProperties": False,
    },
)


class _ScriptedModel:
    def __init__(self, first_call: ToolCall | None = None) -> None:
        self.requests: list[ModelRequest] = []
        self.first_call = first_call or ToolCall(
            "call-1", "multiply", '{"orders":21,"units_per_order":2}'
        )

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelReply((self.first_call,))
        return ModelReply((Text("42"),))


class LangGraphKernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_input_uses_each_framework_as_root_agent(self) -> None:
        for kernel_class in (AgentScopeKernel, LangGraphKernel):
            with self.subTest(kernel=kernel_class.id):
                model = _ScriptedModel()
                executed: list[dict[str, object]] = []

                async def authorize(task: Task, args: dict[str, object]) -> bool:
                    return args == {"orders": 21, "units_per_order": 2}

                async def execute(
                    task: Task,
                    args: dict[str, object],
                    record: list[dict[str, object]] = executed,
                ) -> str:
                    record.append(dict(args))
                    return str(args["orders"] * args["units_per_order"])

                runtime = Runtime(
                    [
                        kernel_class(
                            ModelRouter({"chosen": ModelRoute("upstream", model)}),
                            LocalTools([ReadOnlyTool(SPEC, authorize, execute)]),
                            "Use multiply before answering.",
                        )
                    ]
                )
                task = Task(
                    "same-task", kernel_class.id, "chosen", "21 单，每单 2 件。"
                )
                events = [event async for event in runtime.stream(task)]

                self.assertEqual(len(model.requests), 2)
                self.assertTrue(all(req.model == "upstream" for req in model.requests))
                self.assertEqual(model.requests[0].tools, (SPEC,))
                first_text = [
                    (message.role, part.value)
                    for message in model.requests[0].messages
                    for part in message.parts
                    if isinstance(part, Text)
                ]
                self.assertIn(("system", "Use multiply before answering."), first_text)
                self.assertIn(("user", "21 单，每单 2 件。"), first_text)
                self.assertTrue(
                    any(
                        isinstance(part, ToolResult)
                        and part.id == "call-1"
                        and part.output == "42"
                        for msg in model.requests[1].messages
                        for part in msg.parts
                    )
                )
                self.assertEqual(executed, [{"orders": 21, "units_per_order": 2}])
                self.assertTrue(
                    any(
                        event.kind == "tool_call_delta"
                        and event.payload.get("tool_call_id") == "call-1"
                        and json.loads(str(event.payload.get("delta")))
                        == {"orders": 21, "units_per_order": 2}
                        for event in events
                    )
                )
                self.assertEqual(
                    [event.seq for event in events], list(range(1, len(events) + 1))
                )
                self.assertEqual(events[-1].kind, "reply_finished")
                self.assertEqual(events[-1].payload["finished_reason"], "completed")
                self.assertFalse(any(event.kind == "coverage_gap" for event in events))
                self.assertEqual(
                    sum(event.kind == "model_call_finished" for event in events), 2
                )

    async def test_invalid_tool_arguments_never_execute(self) -> None:
        model = _ScriptedModel(ToolCall("call-1", "multiply", "not JSON"))
        executed = 0

        async def authorize(task: Task, args: dict[str, object]) -> bool:
            return True

        async def execute(task: Task, args: dict[str, object]) -> str:
            nonlocal executed
            executed += 1
            return "42"

        kernel = LangGraphKernel(
            ModelRouter({"chosen": ModelRoute("upstream", model)}),
            LocalTools([ReadOnlyTool(SPEC, authorize, execute)]),
            "Use multiply.",
        )
        with self.assertRaisesRegex(
            UnsupportedLangGraphContent, "invalid tool arguments"
        ):
            _ = [
                event
                async for event in kernel.stream(
                    Task("bad", "langgraph", "chosen", "check")
                )
            ]
        self.assertEqual(executed, 0)

    async def test_product_denial_stops_langgraph_tool(self) -> None:
        executed = 0

        async def authorize(task: Task, args: dict[str, object]) -> bool:
            return False

        async def execute(task: Task, args: dict[str, object]) -> str:
            nonlocal executed
            executed += 1
            return "42"

        kernel = LangGraphKernel(
            ModelRouter({"chosen": ModelRoute("upstream", _ScriptedModel())}),
            LocalTools([ReadOnlyTool(SPEC, authorize, execute)]),
            "Use multiply.",
        )
        with self.assertRaises(PermissionError):
            _ = [
                event
                async for event in kernel.stream(
                    Task("denied", "langgraph", "chosen", "check")
                )
            ]
        self.assertEqual(executed, 0)
