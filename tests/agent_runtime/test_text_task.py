"""One real AgentScope root loop, using synthetic model replies and a local tool."""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum
import json
import unittest

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ThinkingBlock, ToolCallBlock, ToolResultState
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

from xuanyue_agent_runtime import KernelNotRegistered, RuntimeRegistry, TextTaskRequest
from xuanyue_agent_runtime.agentscope_adapter import AgentScopeTextRunner, project_native_event


PRIVATE_THOUGHT = "SYNTHETIC_PRIVATE_THOUGHT_DO_NOT_PERSIST"


class ScriptedModel(ChatModelBase):
    """Only this test knows the planned replies; the SDK Agent/tool loop is real."""

    def __init__(self) -> None:
        super().__init__(CredentialBase(), "synthetic-model", self.Parameters(), stream=False, max_retries=0)
        self.formatter = OpenAIChatFormatter()
        self.replies = iter([
            [
                ThinkingBlock(thinking=PRIVATE_THOUGHT),
                TextBlock(text="先调用已登记的本地工具核对。"),
                ToolCallBlock(name="multiply", id="calc-1", input='{"orders":21,"units_per_order":2}'),
            ],
            [TextBlock(text="合成订单的总件数是 42。")],
        ])

    async def _call_api(self, *args, **kwargs):
        return ChatResponse(content=next(self.replies), is_last=True)


class TextTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_agentscope_root_calls_tool_and_exposes_only_public_events(self) -> None:
        observed_calls: list[tuple[int, int]] = []
        roots: list[Agent] = []
        native_thinking_events = 0

        class FutureEvent:
            type = "FUTURE_EVENT"

            def model_dump(self, **kwargs):
                raise AssertionError("Unknown event contents must not be serialized")

        class ObservedAgent(Agent):
            async def reply_stream(self, *args, **kwargs):
                nonlocal native_thinking_events
                yield FutureEvent()
                async for event in super().reply_stream(*args, **kwargs):
                    event_type = event.type.value if isinstance(event.type, Enum) else event.type
                    if event_type.startswith("THINKING_BLOCK_"):
                        native_thinking_events += 1
                    yield event

        def make_root(request: TextTaskRequest) -> Agent:
            self.assertEqual(request.kernel_id, "agentscope")

            async def multiply(orders: int, units_per_order: int) -> ToolChunk:
                observed_calls.append((orders, units_per_order))
                return ToolChunk(
                    content=[TextBlock(text=str(orders * units_per_order))],
                    state=ToolResultState.SUCCESS,
                )

            tool = FunctionTool(
                func=multiply,
                name="multiply",
                description="Multiply two synthetic order counts.",
                is_read_only=True,
                is_concurrency_safe=True,
                permission=PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="Synthetic local tool",
                ),
            )
            root = ObservedAgent(
                "primary-agent",
                "Use the registered tool to check the synthetic calculation.",
                model=ScriptedModel(),
                toolkit=Toolkit(tools=[tool]),
                injection_config=InjectionConfig(inject_runtime_state=False),
                react_config=ReActConfig(max_iters=3),
            )
            roots.append(root)
            return root

        registry = RuntimeRegistry([AgentScopeTextRunner(make_root)])
        request = TextTaskRequest(
            run_id="run-synthetic-1",
            kernel_id="agentscope",
            text="合成订单有 21 单，每单 2 件。请用工具核对总件数。",
        )
        events = [event async for event in registry.stream(request)]

        self.assertEqual(len(roots), 1)
        self.assertIsInstance(roots[0], Agent)
        self.assertEqual(observed_calls, [(21, 2)])
        self.assertGreater(native_thinking_events, 0)
        self.assertEqual([event.seq for event in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(event.run_id == request.run_id for event in events))
        self.assertIn("42", "".join(str(e.payload.get("delta", "")) for e in events if e.kind == "text_delta"))
        self.assertTrue(any(e.kind == "tool_result_finished" and e.payload.get("state") == "success" for e in events))
        self.assertTrue(any(e.kind == "coverage_gap" and e.payload.get("native_type") == "FUTURE_EVENT" for e in events))
        self.assertNotIn(PRIVATE_THOUGHT, json.dumps([asdict(e) for e in events], ensure_ascii=False))

    async def test_unregistered_kernel_rejects_before_agent_factory_runs(self) -> None:
        factory_calls = 0

        def forbidden_factory(request: TextTaskRequest) -> Agent:
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("No root may be created for an unavailable kernel")

        registry = RuntimeRegistry([AgentScopeTextRunner(forbidden_factory)])
        request = TextTaskRequest(run_id="run-2", kernel_id="langgraph", text="test")
        with self.assertRaises(KernelNotRegistered):
            registry.stream(request)
        self.assertEqual(factory_calls, 0)

    def test_hidden_and_unknown_native_events_are_not_serialized(self) -> None:
        class HiddenEvent:
            type = "THINKING_BLOCK_DELTA"

            def model_dump(self, **kwargs):
                raise AssertionError("Hidden event must not be serialized")

        class FutureEvent:
            type = "FUTURE_EVENT"

            def model_dump(self, **kwargs):
                raise AssertionError("Unknown event must not be serialized")

        self.assertIsNone(project_native_event(HiddenEvent()))
        gap = project_native_event(FutureEvent())
        self.assertIsNotNone(gap)
        self.assertEqual(gap.kind, "coverage_gap")
        self.assertEqual(gap.payload, {"source": "agentscope", "native_type": "FUTURE_EVENT"})


if __name__ == "__main__":
    unittest.main()
