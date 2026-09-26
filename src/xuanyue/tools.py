"""A registered local tool is callable without exposing an SDK tool object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from jsonschema import Draft202012Validator

from .contracts import Task
from .llm import ToolSpec


@dataclass(frozen=True, slots=True)
class ReadOnlyTool:
    spec: ToolSpec
    authorize: Callable[[Task, Mapping[str, object]], Awaitable[bool]]
    execute: Callable[[Task, Mapping[str, object]], Awaitable[str]]

    def __post_init__(self) -> None:
        schema = dict(self.spec.input_schema)
        Draft202012Validator.check_schema(schema)
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError("tool input_schema must define object properties")

    async def invoke(self, task: Task, arguments: Mapping[str, object]) -> str:
        Draft202012Validator(dict(self.spec.input_schema)).validate(dict(arguments))
        if not await self.authorize(task, arguments):
            raise PermissionError(f"tool {self.spec.name!r} was not authorized")
        return await self.execute(task, arguments)
