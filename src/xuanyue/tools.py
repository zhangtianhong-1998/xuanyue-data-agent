"""登记产品工具，并在交给内核前统一校验参数、处理执行失败。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator, ValidationError

from xuanyue.interfaces import ToolService
from xuanyue.types import Task, ToolErrorCode, ToolExecution, ToolSpec

_ERROR_MESSAGES: dict[ToolErrorCode, str] = {
    "invalid_arguments": "工具参数不符合要求，请检查后重试。",
    "permission_denied": "此次工具调用未获授权。",
    "unavailable": "该工具当前不可用。",
    "resource_not_found": "未找到所需资源，请确认后重试。",
    "invalid_result": "工具未返回有效的文字结果。",
    "execution_failed": "工具执行失败，请稍后重试。",
}


def safe_tool_failure(code: ToolErrorCode) -> ToolExecution:
    """只向模型和公开轨迹返回预定义错误，不接受工具提供的任意异常正文。"""
    if code not in _ERROR_MESSAGES:
        code = "execution_failed"
    return ToolExecution(_ERROR_MESSAGES[code], code)


class ToolFailure(Exception):
    """工具可抛出的业务失败；只能选固定错误码，原始异常可留在本机异常链中。"""

    def __init__(self, code: ToolErrorCode) -> None:
        failure = safe_tool_failure(code)
        self.code: ToolErrorCode = failure.error_code or "execution_failed"
        super().__init__(failure.output)


def parse_tool_arguments(spec: ToolSpec, raw: str) -> dict[str, object]:
    """模型桥在 SDK 调度前调用；拒绝非法 JSON、非有限数和 schema 不匹配。

    不允许框架自动修复参数类型。执行时 ToolService 仍须再次校验并授权。
    """
    try:
        arguments = json.loads(raw)
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments must be an object")
        # Python JSON 默认接受 NaN/Infinity；也要拦住 1e999 解析出的无穷值。
        json.dumps(arguments, allow_nan=False)
        Draft202012Validator(spec.input_schema).validate(arguments)
    except (ValueError, TypeError, ValidationError):
        raise ToolFailure("invalid_arguments") from None
    return arguments


async def execute_tool(
    tools: ToolService, task: Task, name: str, arguments: Mapping[str, object]
) -> ToolExecution:
    """两套内核共用的调用边界；SDK 只接收结果，不接触工具原始异常。

    普通失败可交给模型解释或重试，每次重试仍经过授权。取消等 BaseException
    继续向上传播；此处不实现任务取消，也不把成功内容中的 error 字样当作失败。
    """
    try:
        output = await tools.invoke(task, name, arguments)
    except ToolFailure as exc:
        return safe_tool_failure(exc.code)
    except ValidationError:
        return safe_tool_failure("invalid_arguments")
    except PermissionError:
        return safe_tool_failure("permission_denied")
    except ToolUnavailable:
        return safe_tool_failure("unavailable")
    except Exception:  # noqa: BLE001 — 第三方工具异常在此统一转换，正文不向 SDK 传播。
        return safe_tool_failure("execution_failed")
    if not isinstance(output, str):
        # 不调用 str/repr：异常返回对象的转换本身也可能泄露内部内容。
        return safe_tool_failure("invalid_result")
    return ToolExecution(output)


@dataclass(frozen=True, slots=True)
class ReadOnlyTool:
    spec: ToolSpec
    authorize: Callable[[Task, Mapping[str, object]], Awaitable[bool]]
    execute: Callable[[Task, Mapping[str, object]], Awaitable[str]]
    _schema: dict = field(init=False, repr=False)

    def __post_init__(self) -> None:
        schema = deepcopy(dict(self.spec.input_schema))
        Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object" or not isinstance(
            schema.get("properties"), dict
        ):
            raise ValueError("tool input_schema must define object properties")
        object.__setattr__(self, "_schema", schema)

    async def invoke(self, task: Task, arguments: Mapping[str, object]) -> str:
        Draft202012Validator(self._schema).validate(dict(arguments))
        if not await self.authorize(task, arguments):
            raise PermissionError(f"tool {self.spec.name!r} was not authorized")
        return await self.execute(task, arguments)


class ToolUnavailable(LookupError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"tool {name!r} is not registered")


class LocalTools(ToolService):
    def __init__(self, tools: Iterable[ReadOnlyTool]) -> None:
        self._tools: dict[str, ReadOnlyTool] = {}
        for tool in tools:
            name = tool.spec.name
            if not name or name in self._tools:
                raise ValueError(f"invalid or duplicate tool name: {name!r}")
            self._tools[name] = tool

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(
            ToolSpec(tool.spec.name, tool.spec.description, deepcopy(tool._schema))
            for tool in self._tools.values()
        )

    async def invoke(
        self, task: Task, name: str, arguments: Mapping[str, object]
    ) -> str:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ToolUnavailable(name) from exc
        return await tool.invoke(task, arguments)


def multiply_demo_tool(
    *,
    allowed_input: Mapping[str, object] | None = None,
    on_execute: Callable[[Mapping[str, object]], None] | None = None,
) -> ReadOnlyTool:
    """本机界面使用的纯计算样例；业务数据工具另行设计。"""
    spec = ToolSpec(
        "multiply",
        "Multiply an order count by units per order.",
        {
            "type": "object",
            "properties": {
                "orders": {"type": "integer", "minimum": 0, "maximum": 1000000},
                "units_per_order": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000000,
                },
            },
            "required": ["orders", "units_per_order"],
            "additionalProperties": False,
        },
    )

    async def authorize(_task: Task, arguments: Mapping[str, object]) -> bool:
        return allowed_input is None or arguments == allowed_input

    async def execute(_task: Task, arguments: Mapping[str, object]) -> str:
        if on_execute is not None:
            on_execute(arguments)
        return str(arguments["orders"] * arguments["units_per_order"])

    return ReadOnlyTool(spec, authorize, execute)
