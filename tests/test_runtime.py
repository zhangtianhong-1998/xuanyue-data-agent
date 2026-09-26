"""One product task crosses the real AgentScope model and tool adapters."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

from agentscope.message import ThinkingBlock
from jsonschema import ValidationError

from xuanyue import AgentKernel, Event, KernelUnavailable, ModelClient, Runtime, Task
from xuanyue.agentscope import (
    AgentScopeKernel,
    UnsupportedModelContent,
    _AgentScopeModel,
    _model_message,
    project_native_event,
)
from xuanyue.llm import ModelRouter, ModelUnavailable
from xuanyue.tools import LocalTools, ReadOnlyTool
from xuanyue.types import (
    Hint,
    Message,
    ModelReply,
    ModelRequest,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "orders": {"type": "integer"},
        "units_per_order": {"type": "integer"},
    },
    "required": ["orders", "units_per_order"],
    "additionalProperties": False,
}
SPEC = ToolSpec("multiply", "Multiply two synthetic order counts.", SCHEMA)


class ScriptedLLM(ModelClient):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelReply(
                (
                    Text("先核对。"),
                    ToolCall("calc-1", "multiply", '{"orders":21,"units_per_order":2}'),
                )
            )
        return ModelReply((Text("合成订单总件数是 42。"),))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_agentscope_root_uses_product_model_and_tool_interfaces(self) -> None:
        model = ScriptedLLM()
        authorized: list[tuple[str, dict]] = []
        executed: list[tuple[str, dict]] = []

        async def authorize(task: Task, args: dict) -> bool:
            authorized.append((task.run_id, dict(args)))
            return True

        async def execute(task: Task, args: dict) -> str:
            executed.append((task.run_id, dict(args)))
            return str(args["orders"] * args["units_per_order"])

        runtime = Runtime(
            [
                AgentScopeKernel(
                    ModelRouter({"synthetic-small": model}),
                    LocalTools([ReadOnlyTool(SPEC, authorize, execute)]),
                    "Use the tool to check the calculation.",
                )
            ]
        )
        task = Task(
            "run-1",
            "agentscope",
            "synthetic-small",
            "合成订单有 21 单，每单 2 件。请核对总件数。",
        )
        events = [event async for event in runtime.stream(task)]

        self.assertEqual(len(model.requests), 2)
        self.assertTrue(all(request.model == task.model for request in model.requests))
        self.assertEqual(model.requests[0].tools, (SPEC,))
        self.assertEqual(
            authorized, [(task.run_id, {"orders": 21, "units_per_order": 2})]
        )
        self.assertEqual(executed, authorized)
        self.assertEqual(
            [event.seq for event in events], list(range(1, len(events) + 1))
        )
        self.assertTrue(all(event.run_id == task.run_id for event in events))
        self.assertIn(
            "42",
            "".join(
                str(event.payload.get("delta", ""))
                for event in events
                if event.kind == "text_delta"
            ),
        )
        self.assertTrue(
            any(
                event.kind == "tool_result_finished"
                and event.payload.get("state") == "success"
                for event in events
            )
        )
        self.assertTrue(
            any(
                event.kind == "reply_finished"
                and event.payload.get("finished_reason") == "completed"
                for event in events
            )
        )
        self.assertFalse(any(event.kind == "coverage_gap" for event in events))
        self.assertTrue(
            all(
                event.payload.get("usage_status") == "unknown"
                for event in events
                if event.kind == "model_call_finished"
            )
        )
        second_messages = model.requests[1].messages
        assistant_parts = [
            part
            for message in second_messages
            if message.role == "assistant"
            for part in message.parts
        ]
        self.assertTrue(any(isinstance(part, ToolCall) for part in assistant_parts))
        self.assertTrue(
            any(
                isinstance(part, ToolResult)
                and part.output == "42"
                and part.state == "success"
                for part in assistant_parts
            )
        )

    async def test_denied_tool_and_invalid_arguments_never_reach_execute(self) -> None:
        executed = 0

        async def deny(task: Task, args: dict) -> bool:
            return False

        async def execute(task: Task, args: dict) -> str:
            nonlocal executed
            executed += 1
            return "should not run"

        tool = ReadOnlyTool(SPEC, deny, execute)
        task = Task("run-2", "agentscope", "synthetic-small", "test")
        with self.assertRaises(ValidationError):
            await tool.invoke(task, {"orders": "21", "units_per_order": 2})
        with self.assertRaises(PermissionError):
            await tool.invoke(task, {"orders": 21, "units_per_order": 2})
        self.assertEqual(executed, 0)

    async def test_kernel_selection_is_exact(self) -> None:
        class FakeKernel(AgentKernel):
            id = "another-kernel"

            async def stream(self, task: Task):
                yield Event(task.run_id, 1, "reply_finished", {})

        model = ScriptedLLM()
        runtime = Runtime(
            [
                AgentScopeKernel(
                    ModelRouter({"synthetic-small": model}),
                    LocalTools([]),
                    "Answer directly.",
                ),
                FakeKernel(),
            ]
        )
        task = Task("run-3", "another-kernel", "synthetic-small", "test")
        self.assertEqual(
            [event.kind async for event in runtime.stream(task)], ["reply_finished"]
        )
        with self.assertRaises(KernelUnavailable):
            runtime.stream(Task("run-4", "langgraph", "synthetic-small", "test"))
        self.assertEqual(model.requests, [])

    async def test_model_router_selects_exact_model(self) -> None:
        first = ScriptedLLM()
        second = ScriptedLLM()
        router = ModelRouter({"first": first, "second": second})
        await router.complete(ModelRequest("second", (), ()))
        self.assertEqual(first.requests, [])
        self.assertEqual(len(second.requests), 1)
        with self.assertRaises(ModelUnavailable):
            await router.complete(ModelRequest("missing", (), ()))

    async def test_kernel_cannot_switch_the_selected_model(self) -> None:
        backend = ScriptedLLM()
        bridge = _AgentScopeModel("chosen", ModelRouter({"other": backend}), [])
        with self.assertRaises(UnsupportedModelContent):
            await bridge._call_api("other", [])
        self.assertEqual(backend.requests, [])

    async def test_model_receives_no_tool_choice_when_agent_hits_iteration_limit(
        self,
    ) -> None:
        class RepeatedToolLLM(ModelClient):
            def __init__(self) -> None:
                self.requests: list[ModelRequest] = []

            async def complete(self, request: ModelRequest) -> ModelReply:
                self.requests.append(request)
                if request.tool_mode == "none":
                    return ModelReply((Text("已停止继续调用工具。"),))
                return ModelReply(
                    (
                        ToolCall(
                            f"call-{len(self.requests)}",
                            "multiply",
                            '{"orders":1,"units_per_order":1}',
                        ),
                    )
                )

        async def authorize(task: Task, args: dict) -> bool:
            return True

        async def execute(task: Task, args: dict) -> str:
            return "1"

        model = RepeatedToolLLM()
        runtime = Runtime(
            [
                AgentScopeKernel(
                    ModelRouter({"synthetic-small": model}),
                    LocalTools([ReadOnlyTool(SPEC, authorize, execute)]),
                    "Use the tool.",
                )
            ]
        )
        events = [
            event
            async for event in runtime.stream(
                Task("run-limit", "agentscope", "synthetic-small", "Keep checking.")
            )
        ]
        self.assertEqual(model.requests[-1].tool_mode, "none")
        self.assertTrue(
            any(
                isinstance(part, Hint)
                for message in model.requests[-1].messages
                for part in message.parts
            )
        )
        self.assertTrue(any(event.kind == "reply_finished" for event in events))

    async def test_tool_schema_requires_object_properties(self) -> None:
        async def authorize(task: Task, args: dict) -> bool:
            return True

        async def execute(task: Task, args: dict) -> str:
            return "ok"

        with self.assertRaisesRegex(ValueError, "object properties"):
            ReadOnlyTool(
                ToolSpec(
                    "empty",
                    "No args",
                    {"type": "object", "additionalProperties": False},
                ),
                authorize,
                execute,
            )

        schema = deepcopy(SCHEMA)
        gateway = LocalTools(
            [
                ReadOnlyTool(
                    ToolSpec("multiply", "Multiply counts", schema), authorize, execute
                )
            ]
        )
        schema["properties"]["orders"]["type"] = "string"
        advertised = gateway.specs()[0]
        self.assertEqual(
            advertised.input_schema["properties"]["orders"]["type"], "integer"
        )
        with self.assertRaises(ValidationError):
            await gateway.invoke(
                Task("run-schema", "agentscope", "synthetic-small", "test"),
                "multiply",
                {"orders": "21", "units_per_order": 2},
            )

    def test_hidden_and_unknown_native_content_are_handled_explicitly(self) -> None:
        class HiddenEvent:
            type = "THINKING_BLOCK_DELTA"

            def model_dump(self, **kwargs):
                raise AssertionError("hidden event body was read")

        class FutureEvent:
            type = "FUTURE_EVENT"

            def model_dump(self, **kwargs):
                raise AssertionError("unknown event body was read")

        self.assertIsNone(project_native_event(HiddenEvent()))
        self.assertEqual(
            project_native_event(FutureEvent()),
            ("coverage_gap", {"source": "agentscope", "native_type": "FUTURE_EVENT"}),
        )
        native = SimpleNamespace(
            role="assistant", content=[ThinkingBlock(thinking="DO_NOT_PERSIST")]
        )
        message = _model_message(native)
        self.assertEqual(
            message, Message("assistant", (), private_content_omitted=True)
        )
        self.assertNotIn("DO_NOT_PERSIST", json.dumps(asdict(message)))
        with self.assertRaises(UnsupportedModelContent):
            _model_message(SimpleNamespace(role="user", content=[object()]))


if __name__ == "__main__":
    unittest.main()
