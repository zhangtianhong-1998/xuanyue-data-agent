"""同一产品任务通过两种真实 Agent 内核完成合成工具往返。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from xuanyue import Runtime, Task
from xuanyue.engines.agentscope import AgentScopeKernel
from xuanyue.engines.langgraph import LangGraphKernel, UnsupportedLangGraphContent
from xuanyue.llm import ModelRoute, ModelRouter
from xuanyue.tools import LocalTools, ReadOnlyTool, ToolFailure, safe_tool_failure
from xuanyue.types import (
    Image,
    ModelReply,
    ModelRequest,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
)

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


class _TwoToolModel(_ScriptedModel):
    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelReply(
                (
                    ToolCall("call-a", "multiply", '{"orders":2,"units_per_order":3}'),
                    ToolCall("call-b", "multiply", '{"orders":4,"units_per_order":5}'),
                )
            )
        return ModelReply((Text("核对完成。"),))


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
                    "same-task",
                    kernel_class.id,
                    "chosen",
                    "21 单，每单 2 件。",
                    images=(Image("image/png", b"\x89PNG\r\n\x1a\nsynthetic-image"),),
                )
                events = [event async for event in runtime.stream(task)]

                self.assertEqual(len(model.requests), 2)
                self.assertTrue(all(req.model == "upstream" for req in model.requests))
                self.assertEqual(model.requests[0].tools, (SPEC,))
                # 工具往返会再次请求模型，原始用户图片不能在第二次丢失。
                for request in model.requests:
                    image_parts = [
                        part
                        for message in request.messages
                        if message.role == "user"
                        for part in message.parts
                        if isinstance(part, Image)
                    ]
                    self.assertEqual(image_parts, list(task.images))
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
                self.assertEqual(
                    [
                        event.payload["activity_id"]
                        for event in events
                        if event.kind == "model_call_started"
                    ],
                    ["model-1", "model-2"],
                )
                self.assertEqual(
                    [
                        event.payload["activity_id"]
                        for event in events
                        if event.kind == "model_call_finished"
                    ],
                    ["model-1", "model-2"],
                )
                self.assertTrue(
                    all(
                        event.payload.get("activity_id") in {"model-1", "model-2"}
                        for event in events
                        if event.kind == "text_delta"
                    )
                )
                for event in events:
                    if event.kind.startswith(("tool_call_", "tool_result_")):
                        self.assertEqual(event.payload["activity_id"], "tool-1")
                        self.assertEqual(event.payload["parent_activity_id"], "model-1")
                        self.assertEqual(event.payload["tool_call_id"], "call-1")

    async def test_two_tools_keep_distinct_links_to_the_same_model_call(self) -> None:
        for kernel_class in (AgentScopeKernel, LangGraphKernel):
            with self.subTest(kernel=kernel_class.id):
                model = _TwoToolModel()

                async def authorize(task: Task, args: dict[str, object]) -> bool:
                    return True

                async def execute(task: Task, args: dict[str, object]) -> str:
                    return str(args["orders"] * args["units_per_order"])

                kernel = kernel_class(
                    ModelRouter({"chosen": ModelRoute("upstream", model)}),
                    LocalTools([ReadOnlyTool(SPEC, authorize, execute)]),
                    "Check both calculations.",
                )
                events = [
                    event
                    async for event in Runtime([kernel]).stream(
                        Task("two-tools", kernel.id, "chosen", "核对两组乘法")
                    )
                ]
                expected = {"call-a": "tool-1", "call-b": "tool-2"}
                self.assertEqual(len(model.requests), 2)
                self.assertEqual(
                    [
                        event.payload["activity_id"]
                        for event in events
                        if event.kind == "model_call_started"
                    ],
                    ["model-1", "model-2"],
                )
                for kind in (
                    "tool_call_started",
                    "tool_call_delta",
                    "tool_call_finished",
                    "tool_result_started",
                    "tool_result_delta",
                    "tool_result_finished",
                ):
                    actual = [event for event in events if event.kind == kind]
                    self.assertEqual(len(actual), 2, (kernel.id, kind))
                    for event in actual:
                        self.assertEqual(
                            event.payload["activity_id"],
                            expected[event.payload["tool_call_id"]],
                        )
                        self.assertEqual(event.payload["parent_activity_id"], "model-1")

    async def test_langgraph_rejects_tool_result_without_a_model_call(self) -> None:
        class OrphanGraph:
            async def astream(self, *_args, **_kwargs):
                yield (
                    "updates",
                    {
                        "tools": {
                            "messages": [
                                ToolMessage(
                                    content="orphan",
                                    tool_call_id="missing",
                                    name="multiply",
                                )
                            ]
                        }
                    },
                )

        kernel = LangGraphKernel(
            ModelRouter({"chosen": ModelRoute("upstream", _ScriptedModel())}),
            LocalTools([]),
            "Answer directly.",
        )
        with (
            patch("xuanyue.engines.langgraph.create_agent", return_value=OrphanGraph()),
            self.assertRaisesRegex(
                UnsupportedLangGraphContent, "no pending parent call"
            ),
        ):
            _ = [
                event
                async for event in kernel.stream(
                    Task("orphan", "langgraph", "chosen", "check")
                )
            ]

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
        with self.assertRaises(ToolFailure) as failure:
            _ = [
                event
                async for event in kernel.stream(
                    Task("bad", "langgraph", "chosen", "check")
                )
            ]
        self.assertEqual(failure.exception.code, "invalid_arguments")
        self.assertEqual(executed, 0)

    async def test_product_denial_returns_safe_error_without_executing(self) -> None:
        executed = 0
        model = _ScriptedModel()

        async def authorize(task: Task, args: dict[str, object]) -> bool:
            return False

        async def execute(task: Task, args: dict[str, object]) -> str:
            nonlocal executed
            executed += 1
            return "42"

        kernel = LangGraphKernel(
            ModelRouter({"chosen": ModelRoute("upstream", model)}),
            LocalTools([ReadOnlyTool(SPEC, authorize, execute)]),
            "Use multiply.",
        )
        events = [
            event
            async for event in kernel.stream(
                Task("denied", "langgraph", "chosen", "check")
            )
        ]
        self.assertEqual(executed, 0)
        self.assertEqual(len(model.requests), 2)
        results = [
            part
            for message in model.requests[1].messages
            for part in message.parts
            if isinstance(part, ToolResult)
        ]
        self.assertEqual(
            results,
            [
                ToolResult(
                    "call-1",
                    "multiply",
                    safe_tool_failure("permission_denied").output,
                    "error",
                )
            ],
        )
        self.assertEqual(
            [
                event.payload["state"]
                for event in events
                if event.kind == "tool_result_finished"
            ],
            ["error"],
        )
