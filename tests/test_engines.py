"""引擎注册只决定构造，任务仍由 Runtime 按用户指定名称派发。"""

from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from unittest.mock import patch

from xuanyue.engines import EngineNameConflict, EngineRegistry, default_engine_registry
from xuanyue.runtime import InvalidKernelEvent, KernelUnavailable, Runtime
from xuanyue.types import Event, Task


class _FakeEngine:
    id = "third_party"

    def __init__(self, model: object, tools: object, system_prompt: str) -> None:
        self.received = (model, tools, system_prompt)

    async def stream(self, task: Task) -> AsyncIterator[Event]:
        yield Event(task.run_id, 1, "reply_finished", {"finished_reason": "completed"})


class _InstalledPoint:
    name = "third_party"

    def __init__(self) -> None:
        self.loads = 0

    def load(self):
        self.loads += 1
        return _FakeEngine


class EngineRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_third_party_engine_runs_as_root(self) -> None:
        registry = EngineRegistry()
        registry.register("third_party", _FakeEngine)
        model, tools = object(), object()
        engine = registry.create("third_party", model, tools, "Follow the task.")
        self.assertEqual(engine.received, (model, tools, "Follow the task."))

        task = Task("run-1", "third_party", "chosen", "Hello")
        events = [event async for event in Runtime([engine]).stream(task)]
        self.assertEqual(
            events,
            [Event("run-1", 1, "reply_finished", {"finished_reason": "completed"})],
        )

    async def test_unknown_duplicate_and_wrong_factory_id_fail_closed(self) -> None:
        registry = EngineRegistry()
        with self.assertRaises(KernelUnavailable):
            registry.create("missing", object(), object(), "prompt")
        with self.assertRaises(ValueError):
            registry.register("", _FakeEngine)
        registry.register("third_party", _FakeEngine)
        with self.assertRaises(EngineNameConflict) as conflict:
            registry.register("third_party", _FakeEngine)
        self.assertEqual(conflict.exception.name, "third_party")
        registry.register("wrong", _FakeEngine)
        with self.assertRaises(TypeError):
            registry.create("wrong", object(), object(), "prompt")

    async def test_installed_entry_point_is_loaded_only_when_selected(self) -> None:
        point = _InstalledPoint()
        with patch("xuanyue.engines.entry_points", return_value=[point]) as discover:
            registry = default_engine_registry()
        discover.assert_called_once_with(group="xuanyue.agent_engines")
        self.assertEqual(registry.names, ("agentscope", "langgraph", "third_party"))
        self.assertEqual(point.loads, 0)
        engine = registry.create("third_party", object(), object(), "prompt")
        self.assertEqual(engine.id, "third_party")
        self.assertEqual(point.loads, 1)

    async def test_runtime_rejects_invalid_extension_event_envelopes(self) -> None:
        task = Task("run-1", "third_party", "chosen", "Hello")
        malformed = (
            Event("wrong-run", 1, "reply_finished", {}),
            Event("run-1", 2, "reply_finished", {}),
            Event("run-1", 1, "reply_finished", []),
            "not an event",
        )
        for invalid in malformed:
            with self.subTest(invalid=type(invalid).__name__):

                class BrokenEngine:
                    id = "third_party"

                    def __init__(self, value: object) -> None:
                        self._value = value

                    async def stream(self, requested: Task):
                        yield self._value

                with self.assertRaises(InvalidKernelEvent):
                    _ = [
                        event
                        async for event in Runtime([BrokenEngine(invalid)]).stream(task)
                    ]
