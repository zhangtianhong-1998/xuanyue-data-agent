"""K0b slice 1: run an AgentScope root agent with synthetic inputs only."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from enum import Enum
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import TextBlock, ThinkingBlock, ToolCallBlock, ToolResultState, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk, Toolkit


HERE = Path(__file__).resolve().parent
LOCK = HERE.parent / "framework-comparison" / "agentscope" / "requirements.lock"
GOAL = "合成订单有 21 单，每单 2 件。请用已登记工具核对总件数。"
SYNTHETIC_THOUGHT = "SYNTHETIC_PRIVATE_THOUGHT_DO_NOT_PERSIST"

# Only these public fields may leave the framework event object in this spike.
PUBLIC_FIELDS = {
    "MODEL_CALL_START": {"model_name"},
    "TEXT_BLOCK_DELTA": {"delta"},
    "TOOL_CALL_START": {"tool_call_id", "tool_call_name"},
    "TOOL_CALL_DELTA": {"tool_call_id", "delta"},
    "TOOL_RESULT_TEXT_DELTA": {"tool_call_id", "delta"},
    "TOOL_RESULT_END": {"tool_call_id", "state"},
    "REPLY_END": set(),
}


class UnsupportedKernel(Exception):
    """The selected kernel is unavailable in this one-kernel experiment."""


class ScriptedModel(ChatModelBase):
    """Replace only the model; the AgentScope agent and tool loop stay real."""

    def __init__(self) -> None:
        super().__init__(CredentialBase(), "synthetic-model", self.Parameters(), stream=False, max_retries=0)
        self.formatter = OpenAIChatFormatter()
        self.replies = iter([
            [
                ThinkingBlock(thinking=SYNTHETIC_THOUGHT),
                TextBlock(text="先核对订单数和每单件数，再调用本地计算工具。"),
                ToolCallBlock(name="multiply", id="calc-1", input='{"orders":21,"units_per_order":2}'),
            ],
            [TextBlock(text="合成订单的总件数是 42。")],
        ])

    async def _call_api(self, *args, **kwargs):
        return ChatResponse(content=next(self.replies), is_last=True)


def project_public_event(event) -> dict | None:
    event_type = event.type.value if isinstance(event.type, Enum) else event.type
    fields = PUBLIC_FIELDS.get(event_type)
    if fields is None:
        return None
    return event.model_dump(mode="json", include={"type", *fields})


def make_root(selected_kernel: str, observed_calls: list[dict]) -> Agent:
    if selected_kernel != "agentscope":
        raise UnsupportedKernel(f"selected kernel {selected_kernel!r} is not registered in this spike")

    async def multiply(orders: int, units_per_order: int) -> ToolChunk:
        observed_calls.append({"orders": orders, "units_per_order": units_per_order})
        return ToolChunk(content=[TextBlock(text=str(orders * units_per_order))], state=ToolResultState.SUCCESS)

    tool = FunctionTool(
        func=multiply,
        name="multiply",
        description="Multiply two synthetic order counts.",
        is_read_only=True,
        is_concurrency_safe=True,
        permission=PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Synthetic local tool"),
    )
    return Agent(
        "primary-agent",
        "Use the registered tool to check the user's synthetic calculation.",
        model=ScriptedModel(),
        toolkit=Toolkit(tools=[tool]),
        injection_config=InjectionConfig(inject_runtime_state=False),
        react_config=ReActConfig(max_iters=3),
    )


async def execute(selected_kernel: str) -> dict:
    observed_calls: list[dict] = []
    root = make_root(selected_kernel, observed_calls)
    public_events = []
    native_thinking_events = 0
    async for native_event in root.reply_stream(UserMsg("user", GOAL)):
        native_type = native_event.type.value if isinstance(native_event.type, Enum) else native_event.type
        if native_type.startswith("THINKING_BLOCK_"):
            native_thinking_events += 1
        projected = project_public_event(native_event)
        if projected is not None:
            public_events.append({"seq": len(public_events) + 1, **projected})

    visible_text = "".join(e.get("delta", "") for e in public_events if e["type"] == "TEXT_BLOCK_DELTA")
    assert observed_calls == [{"orders": 21, "units_per_order": 2}], observed_calls
    assert "先核对" in visible_text and "42" in visible_text, visible_text
    assert any(e["type"] == "TOOL_RESULT_END" and e["state"] == "success" for e in public_events)
    assert type(root).__module__.startswith("agentscope."), type(root)
    assert not any(name == "langgraph" or name.startswith("langgraph.") for name in sys.modules)
    assert native_thinking_events > 0
    assert SYNTHETIC_THOUGHT not in json.dumps(public_events)

    try:
        make_root("langgraph", observed_calls)
    except UnsupportedKernel:
        pass
    else:
        raise AssertionError("An unavailable selected kernel was silently replaced")
    assert len(observed_calls) == 1

    class HiddenEvent:
        type = "THINKING_BLOCK_DELTA"

        def model_dump(self, **kwargs):
            raise AssertionError("Hidden event must never be serialized")

    assert project_public_event(HiddenEvent()) is None
    assert all(set(event) <= {"seq", "type"} | PUBLIC_FIELDS[event["type"]] for event in public_events)

    return {
        "status": "passed",
        "selected_kernel": selected_kernel,
        "actual_root_class": f"{type(root).__module__}.{type(root).__name__}",
        "model": "synthetic-model",
        "goal": GOAL,
        "tool_calls": observed_calls,
        "checks": ["root_agent", "public_text_and_tool_result", "no_silent_kernel_swap", "synthetic_thinking_event_excluded"],
        "native_thinking_event_count": native_thinking_events,
        "public_events": public_events,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", default="agentscope")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = {
        "experiment": "agentscope-primary-k0b-slice-1",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "framework_version": importlib.metadata.version("agentscope"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dependency_lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
        "no_external_model_calls": True,
        "selected_kernel": args.kernel,
        "not_tested": ["real_model_planning", "model_uses_tool_result", "sensitive_value_redaction", "delegation", "pause_and_resume", "branching", "A2A", "desktop_ui"],
    }
    try:
        report.update(asyncio.run(execute(args.kernel)))
    except UnsupportedKernel:
        report.update({"status": "unsupported", "actual_root_class": None, "tool_calls": [], "public_events": [],
                       "reason": "selected kernel is not registered in this spike"})
    except Exception as exc:
        report.update({"status": "failed", "error_type": type(exc).__name__})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "output": str(args.output)}, ensure_ascii=False))
    return {"passed": 0, "failed": 1, "unsupported": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
