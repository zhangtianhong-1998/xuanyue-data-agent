"""登记可运行的 Agent 引擎，并按任务指定的名称创建一个根引擎。

每个引擎实现 AgentKernel：接收产品 Task，使用传入的 ModelClient、ToolService，
再把框架事件转为产品 Event。已安装的扩展包可用 xuanyue.agent_engines
入口点提供同样签名的工厂；发现入口点不会运行该扩展，选中时才加载。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from importlib.metadata import entry_points

from xuanyue.interfaces import AgentKernel, ModelClient, ToolService
from xuanyue.runtime import KernelUnavailable

EngineFactory = Callable[[ModelClient, ToolService, str], AgentKernel]
_ENTRY_POINT_GROUP = "xuanyue.agent_engines"
_ENGINE_NAME = re.compile(r"[a-z][a-z0-9_.-]*\Z")


class EngineNameConflict(ValueError):
    """两个引擎登记了相同名称；保留名称供 CLI 安全定位冲突。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"engine {name!r} is already registered")


class EngineRegistry:
    """只负责登记和构造；Runtime 仍按 Task.kernel 精确派发。"""

    def __init__(self) -> None:
        self._factories: dict[str, EngineFactory] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def register(self, name: str, factory: EngineFactory) -> None:
        """拒绝空名和重复名，避免扩展包悄悄覆盖内置引擎。"""
        if not isinstance(name, str) or not _ENGINE_NAME.fullmatch(name):
            raise ValueError("engine name must start with a-z and use a-z, 0-9, ._-")
        if name in self._factories:
            raise EngineNameConflict(name)
        if not callable(factory):
            raise TypeError("engine factory must be callable")
        self._factories[name] = factory

    def create(
        self, name: str, model: ModelClient, tools: ToolService, system_prompt: str
    ) -> AgentKernel:
        """构造选定引擎；工厂返回别的内核名时明确失败，不替用户改选。"""
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KernelUnavailable(name) from exc
        engine = factory(model, tools, system_prompt)
        if getattr(engine, "id", None) != name or not callable(
            getattr(engine, "stream", None)
        ):
            raise TypeError(f"engine factory for {name!r} returned an invalid engine")
        return engine


def _agentscope_engine(
    model: ModelClient, tools: ToolService, system_prompt: str
) -> AgentKernel:
    # 延迟导入：只选 LangGraph 时不必加载 AgentScope，反之亦然。
    from xuanyue.engines.agentscope import AgentScopeKernel

    return AgentScopeKernel(model, tools, system_prompt)


def _langgraph_engine(
    model: ModelClient, tools: ToolService, system_prompt: str
) -> AgentKernel:
    from xuanyue.engines.langgraph import LangGraphKernel

    return LangGraphKernel(model, tools, system_prompt)


def default_engine_registry() -> EngineRegistry:
    """登记两个内置引擎，再发现已安装扩展包声明的工厂。"""
    registry = EngineRegistry()
    registry.register("agentscope", _agentscope_engine)
    registry.register("langgraph", _langgraph_engine)
    for point in entry_points(group=_ENTRY_POINT_GROUP):

        def external_engine(
            model: ModelClient,
            tools: ToolService,
            system_prompt: str,
            *,
            selected_point=point,
        ) -> AgentKernel:
            factory = selected_point.load()
            if not callable(factory):
                raise TypeError("installed engine entry point must be callable")
            return factory(model, tools, system_prompt)

        registry.register(point.name, external_engine)
    return registry
