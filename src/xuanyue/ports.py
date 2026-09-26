"""产品与 Agent 内核之间的能力接口；这里不执行任务。

调用方向：Runtime 调用 AgentKernel；内核适配器调用产品提供的 ModelPort、ToolPort。
新增内核还须转换自己的模型、工具和事件；当前接口只覆盖文字任务。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from .types import Event, ModelReply, ModelRequest, Task, ToolSpec


class AgentKernel(Protocol):
    """主 Agent 的最小运行接口；当前只约定文字任务的事件流。"""

    id: str

    def stream(self, task: Task) -> AsyncIterator[Event]: ...


class ModelPort(Protocol):
    """模型调用接口；内核提交统一请求，产品决定具体模型服务。"""

    async def complete(self, request: ModelRequest) -> ModelReply: ...


class ToolPort(Protocol):
    """工具目录与调用接口；内核不能绕过产品直接执行工具。"""

    def specs(self) -> tuple[ToolSpec, ...]: ...

    async def invoke(
        self, task: Task, name: str, arguments: Mapping[str, object]
    ) -> str:
        """具体实现须在每次调用时重新校验参数和授权。"""
        ...
