"""把 AgentScope 2.0.8 接到产品的主任务、模型、工具和事件接口。

当前只处理文字消息、文本工具结果和按只读约定登记的工具；
不支持的消息或模型选项明确报错。未知 SDK 事件只报告覆盖缺口，不复制原始内容。
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping, Sequence
from enum import Enum
from typing import Any

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import (
    HintBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChoice, ToolChunk, Toolkit

from .interfaces import AgentKernel, ModelClient, ToolService
from .types import (
    Event,
    Hint,
    Message,
    ModelReply,
    ModelRequest,
    Task,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class UnsupportedModelContent(ValueError):
    """当前适配器无法表示某种消息、模型选项或工具调用。"""


def _model_message(native: Msg) -> Message:
    """把 SDK 消息转成产品消息；只接受当前产品类型能表达的内容。"""
    parts = []
    private_content_omitted = False
    for block in native.content:
        if isinstance(block, TextBlock):
            parts.append(Text(block.text))
        elif isinstance(block, ToolCallBlock):
            parts.append(ToolCall(block.id, block.name, block.input))
        elif isinstance(block, ToolResultBlock):
            output = block.output
            if isinstance(output, list):
                # 产品的工具结果只有一个文本输出，不能把多段或非文字结果悄悄拼接。
                if len(output) != 1 or not isinstance(output[0], TextBlock):
                    raise UnsupportedModelContent(
                        "tool result with multiple or non-text blocks"
                    )
                output = output[0].text
            state = block.state.value if isinstance(block.state, Enum) else block.state
            parts.append(ToolResult(block.id, block.name, output, state))
        elif isinstance(block, ThinkingBlock):
            # 隐藏推理不进入产品模型请求；仅标记这条消息曾省略私有内容。
            private_content_omitted = True
            continue
        elif isinstance(block, HintBlock) and isinstance(block.hint, str):
            parts.append(Hint(block.hint, block.source))
        else:
            raise UnsupportedModelContent(
                f"unsupported message block: {type(block).__name__}"
            )
    return Message(native.role, tuple(parts), private_content_omitted)


def _tool_specs(native_tools: list[dict] | None) -> tuple[ToolSpec, ...]:
    """把 SDK 的函数工具定义转成产品类型；其他工具种类暂不支持。"""
    specs = []
    for item in native_tools or []:
        if item.get("type") != "function" or not isinstance(item.get("function"), dict):
            raise UnsupportedModelContent("unsupported tool specification")
        function = item["function"]
        name = function.get("name")
        description = function.get("description")
        schema = function.get("parameters")
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not isinstance(schema, dict)
        ):
            raise UnsupportedModelContent("invalid function tool specification")
        specs.append(ToolSpec(name, description, schema))
    return tuple(specs)


class _AgentScopeModel(ChatModelBase):
    """实现 SDK 模型接口，将实际请求交给产品的 ModelClient。"""

    def __init__(
        self, model: str, model_client: ModelClient, allowed_tools: Sequence[ToolSpec]
    ) -> None:
        super().__init__(
            CredentialBase(), model, self.Parameters(), stream=False, max_retries=0
        )
        self.formatter = OpenAIChatFormatter()
        self._model_client = model_client
        self._allowed_tools = {tool.name: tool for tool in allowed_tools}

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """校验本轮模型和工具范围，再完成 SDK 与产品模型消息的双向转换。"""
        # 主任务指定的模型在运行期间不能由内核改成另一个模型。
        if model_name != self.model:
            raise UnsupportedModelContent(
                "kernel changed the model selected for this task"
            )
        # 当前只支持 auto/none；不认识的选项直接拒绝，避免调用时被悄悄忽略。
        if kwargs or (
            tool_choice is not None
            and (
                tool_choice.mode not in ("auto", "none")
                or tool_choice.tools is not None
            )
        ):
            raise UnsupportedModelContent(
                "this tool choice or model option is not supported in this slice"
            )
        tool_mode = tool_choice.mode if tool_choice is not None else "auto"
        specs = _tool_specs(tools)
        # SDK 本轮传来的工具，必须与产品登记的同名工具定义一致。
        if any(self._allowed_tools.get(spec.name) != spec for spec in specs):
            raise UnsupportedModelContent(
                "kernel requested a tool absent from the product registry"
            )
        reply = await self._model_client.complete(
            ModelRequest(
                model_name,
                tuple(_model_message(msg) for msg in messages),
                specs,
                tool_mode,
            ),
        )
        if not isinstance(reply, ModelReply):
            raise TypeError("ModelClient.complete must return ModelReply")
        # 回写 SDK 前再次限制工具调用，模型回复也不能越过本轮的工具范围。
        blocks = []
        for part in reply.parts:
            if isinstance(part, Text):
                blocks.append(TextBlock(text=part.value))
            elif isinstance(part, ToolCall):
                if tool_mode == "none":
                    raise UnsupportedModelContent(
                        "model called a tool after tools were disabled"
                    )
                if part.name not in {spec.name for spec in specs}:
                    raise UnsupportedModelContent(
                        f"model called unavailable tool {part.name!r}"
                    )
                blocks.append(
                    ToolCallBlock(id=part.id, name=part.name, input=part.arguments)
                )
            else:
                raise UnsupportedModelContent(
                    f"unsupported model reply part: {type(part).__name__}"
                )
        return ChatResponse(content=blocks, is_last=True)


def _native_tool(spec: ToolSpec, task: Task, tools: ToolService) -> FunctionTool:
    """包装产品工具供 SDK 调用；参数校验和逐次授权留在 ToolService。"""

    async def invoke(**kwargs: object) -> ToolChunk:
        result = await tools.invoke(task, spec.name, kwargs)
        if not isinstance(result, str):
            raise TypeError(f"tool {spec.name!r} must return text")
        return ToolChunk(
            content=[TextBlock(text=result)], state=ToolResultState.SUCCESS
        )

    # SDK 的静态 ALLOW 只放行包装函数；is_read_only 也不能替代逐次授权和执行控制。
    return FunctionTool(
        func=invoke,
        name=spec.name,
        description=spec.description,
        input_schema=dict(spec.input_schema),
        is_read_only=True,
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Product-registered read-only tool",
        ),
    )


# 只映射产品轨迹需要的事件类型与公开字段；未列出的原生字段不予序列化。
_PUBLIC_EVENTS: Mapping[str, tuple[str, frozenset[str]]] = {
    "REPLY_START": ("reply_started", frozenset({"name"})),
    "MODEL_CALL_START": ("model_call_started", frozenset({"model_name"})),
    "MODEL_CALL_END": ("model_call_finished", frozenset({"finished_reason"})),
    "TEXT_BLOCK_DELTA": ("text_delta", frozenset({"delta"})),
    "TOOL_CALL_START": (
        "tool_call_started",
        frozenset({"tool_call_id", "tool_call_name"}),
    ),
    "TOOL_CALL_DELTA": ("tool_call_delta", frozenset({"tool_call_id", "delta"})),
    "TOOL_CALL_END": ("tool_call_finished", frozenset({"tool_call_id"})),
    "TOOL_RESULT_START": (
        "tool_result_started",
        frozenset({"tool_call_id", "tool_call_name"}),
    ),
    "TOOL_RESULT_TEXT_DELTA": (
        "tool_result_delta",
        frozenset({"tool_call_id", "delta"}),
    ),
    "TOOL_RESULT_END": ("tool_result_finished", frozenset({"tool_call_id", "state"})),
    "REPLY_END": ("reply_finished", frozenset({"finished_reason"})),
}
_SKIPPED_EVENTS = {"TEXT_BLOCK_START", "TEXT_BLOCK_END"}
_SAFE_EVENT_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


def project_native_event(event: object) -> tuple[str, Mapping[str, object]] | None:
    """投影公开事件；返回 None 表示该原生事件被有意忽略。

    隐藏推理不会输出；未知事件只暴露安全的类型名，不复制原始载荷。
    """
    native_type = getattr(event, "type", None)
    if isinstance(native_type, Enum):
        native_type = native_type.value
    if isinstance(native_type, str) and native_type.startswith("THINKING_BLOCK_"):
        return None
    if native_type in _SKIPPED_EVENTS:
        return None
    public_spec = (
        _PUBLIC_EVENTS.get(native_type) if isinstance(native_type, str) else None
    )
    if public_spec is None:
        # SDK 增加新事件时保留覆盖缺口，让后续适配有据可查。
        safe_type = (
            native_type
            if isinstance(native_type, str) and _SAFE_EVENT_TYPE.fullmatch(native_type)
            else "unrecognized"
        )
        return "coverage_gap", {"source": "agentscope", "native_type": safe_type}
    kind, fields = public_spec
    dumped = event.model_dump(mode="json", include=fields)
    payload = {name: dumped[name] for name in fields if name in dumped}
    if native_type == "MODEL_CALL_END":
        # SDK 默认的零用量不是供应商实测值，当前只能记为未知。
        payload["usage_status"] = "unknown"
    return kind, payload


class AgentScopeKernel(AgentKernel):
    """让 AgentScope 独立担任主内核，对外只交付产品事件。"""

    id = "agentscope"

    def __init__(
        self, model: ModelClient, tools: ToolService, system_prompt: str
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt must be non-empty")
        self._model = model
        self._tools = tools
        self._system_prompt = system_prompt

    async def stream(self, task: Task) -> AsyncIterator[Event]:
        """每次任务新建根 Agent，并只输出经过公开投影的事件。"""
        if task.kernel != self.id:
            raise ValueError("task kernel does not match AgentScope")
        specs = self._tools.specs()
        names = [spec.name for spec in specs]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("tool names must be non-empty and unique")
        # 不复用 SDK Agent 状态；当前每个任务最多执行三轮 ReAct。
        root = Agent(
            "primary-agent",
            self._system_prompt,
            model=_AgentScopeModel(task.model, self._model, specs),
            toolkit=Toolkit(
                tools=[_native_tool(spec, task, self._tools) for spec in specs]
            ),
            injection_config=InjectionConfig(inject_runtime_state=False),
            react_config=ReActConfig(max_iters=3),
        )
        # 只给实际输出的事件编号；被过滤的 SDK 事件不占序号。
        seq = 0
        async for native_event in root.reply_stream(UserMsg("user", task.text)):
            projected = project_native_event(native_event)
            if projected is None:
                continue
            seq += 1
            kind, payload = projected
            yield Event(task.run_id, seq, kind, payload)
