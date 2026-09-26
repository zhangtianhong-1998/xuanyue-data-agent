"""Functional boundaries shared by the product and Agent kernels."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from .types import Event, ModelReply, ModelRequest, Task, ToolSpec


class AgentKernel(Protocol):
    id: str

    def stream(self, task: Task) -> AsyncIterator[Event]: ...


class ModelPort(Protocol):
    async def complete(self, request: ModelRequest) -> ModelReply: ...


class ToolPort(Protocol):
    def specs(self) -> tuple[ToolSpec, ...]: ...

    async def invoke(
        self, task: Task, name: str, arguments: Mapping[str, object]
    ) -> str: ...
