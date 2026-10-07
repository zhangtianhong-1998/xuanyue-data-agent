"""产品与 Agent 内核之间的能力接口；这里不执行任务。

调用方向：Runtime 调用 AgentKernel；内核适配器调用产品提供的 ModelClient、ToolService。
新增内核还须转换自己的模型、工具和事件；当前接口覆盖文字、用户图片和文本工具结果。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Protocol

from xuanyue.types import (
    Event,
    ModelReply,
    ModelRequest,
    ModelStreamChunk,
    Task,
    ToolSpec,
)


class AgentKernel(Protocol):
    """可替换 Agent 引擎的运行接口；当前只约定主任务的公开事件流。"""

    id: str

    def stream(self, task: Task) -> AsyncIterator[Event]: ...


class ModelClient(Protocol):
    """模型调用接口；内核提交统一请求，产品决定具体模型服务。"""

    async def complete(self, request: ModelRequest) -> ModelReply: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        """文字可先到达；最后一个 chunk 必须带完整且已校验的回复。"""
        ...


class ToolService(Protocol):
    """工具目录与调用接口；内核不能绕过产品直接执行工具。"""

    def specs(self) -> tuple[ToolSpec, ...]: ...

    async def invoke(
        self, task: Task, name: str, arguments: Mapping[str, object]
    ) -> str:
        """实现须逐次校验、授权；适配器经 execute_tool 调用，避免 SDK 转发异常正文。"""
        ...
