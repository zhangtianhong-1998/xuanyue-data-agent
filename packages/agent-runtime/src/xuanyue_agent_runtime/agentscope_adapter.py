"""AgentScope 2.0.8 adapter for one selected text root task."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping

from agentscope.agent import Agent
from agentscope.message import UserMsg

from .task import PublicEvent, TextTaskRequest


# Map only understood public SDK events to product-facing event names and fields.
_PUBLIC_EVENTS: Mapping[str, tuple[str, frozenset[str]]] = {
    "MODEL_CALL_START": ("model_call_started", frozenset({"model_name"})),
    "TEXT_BLOCK_DELTA": ("text_delta", frozenset({"delta"})),
    "TOOL_CALL_START": ("tool_call_started", frozenset({"tool_call_id", "tool_call_name"})),
    "TOOL_CALL_DELTA": ("tool_call_delta", frozenset({"tool_call_id", "delta"})),
    "TOOL_RESULT_TEXT_DELTA": ("tool_result_delta", frozenset({"tool_call_id", "delta"})),
    "TOOL_RESULT_END": ("tool_result_finished", frozenset({"tool_call_id", "state"})),
    "REPLY_END": ("reply_finished", frozenset()),
}
_SAFE_EVENT_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class _ProjectedEvent:
    kind: str
    payload: Mapping[str, object]


def _native_type(event: object) -> str | None:
    value = getattr(event, "type", None)
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) else None


def project_native_event(event: object) -> _ProjectedEvent | None:
    """Never serialize an unknown or hidden native event body."""

    native_type = _native_type(event)
    if native_type is not None and native_type.startswith("THINKING_BLOCK_"):
        return None

    public_spec = _PUBLIC_EVENTS.get(native_type) if native_type is not None else None
    if public_spec is None:
        # The gap is visible, but its raw contents never leave the SDK boundary.
        safe_type = native_type if native_type and _SAFE_EVENT_TYPE.fullmatch(native_type) else "unrecognized"
        return _ProjectedEvent("coverage_gap", {"source": "agentscope", "native_type": safe_type})

    kind, fields = public_spec
    dumped = event.model_dump(mode="json", include=fields)
    return _ProjectedEvent(kind, {name: dumped[name] for name in fields if name in dumped})


class AgentScopeTextRunner:
    """Run a caller-built AgentScope root; model and tools are not chosen here."""

    kernel_id = "agentscope"

    def __init__(self, agent_factory: Callable[[TextTaskRequest], Agent]) -> None:
        self._agent_factory = agent_factory

    async def stream(self, request: TextTaskRequest) -> AsyncIterator[PublicEvent]:
        if request.kernel_id != self.kernel_id:
            raise ValueError("request kernel does not match AgentScope adapter")
        root = self._agent_factory(request)
        if not isinstance(root, Agent):
            raise TypeError("agent_factory must return an AgentScope Agent")

        seq = 0
        async for native_event in root.reply_stream(UserMsg("user", request.text)):
            projected = project_native_event(native_event)
            if projected is None:
                continue
            seq += 1
            yield PublicEvent(request.run_id, seq, projected.kind, projected.payload)
