"""A registered local tool is callable without exposing an SDK tool object."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from .interfaces import ToolService
from .types import Task, ToolSpec


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
