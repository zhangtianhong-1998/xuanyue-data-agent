"""工具失败的双内核验收：检查真实模型请求、公开事件与 SQLite 记录。

使用本地合成模型和临时数据库；敏感标记只出现在异常正文或非法返回值，
不会放入本就应该公开的用户输入、工具参数和模型答复。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from jsonschema import ValidationError

from xuanyue.chat import ChatService
from xuanyue.engines.agentscope import AgentScopeKernel
from xuanyue.engines.langgraph import LangGraphKernel
from xuanyue.llm import ModelRoute, ModelRouter
from xuanyue.runtime import Runtime
from xuanyue.storage import LocalStore
from xuanyue.tools import (
    LocalTools,
    ReadOnlyTool,
    ToolFailure,
    execute_tool,
    safe_tool_failure,
)
from xuanyue.types import (
    ModelReply,
    ModelRequest,
    Task,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
)

_KERNELS = (AgentScopeKernel, LangGraphKernel)
_SECRET = "SYNTHETIC_TOOL_EXCEPTION_BODY_DO_NOT_EXPOSE"
_ANSWER = "工具未完成的部分已说明；本轮答复结束。"
_SPEC = ToolSpec(
    "inspect_value",
    "Read a synthetic integer value.",
    {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    },
)


class _ScriptedModel:
    """第一轮发出工具调用，第二轮固定答复，同时保留路由后的真实请求。"""

    def __init__(self, calls: tuple[ToolCall, ...]) -> None:
        self.calls = calls
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelReply(self.calls)
        return ModelReply((Text(_ANSWER),))


async def _allow(_task, _arguments) -> bool:
    return True


async def _observe(kernel_class, tools, calls):
    """复用产品消费与终态保存；另读 SQLite 逻辑记录，避免只检查 UI 投影。"""
    model = _ScriptedModel(calls)
    with tempfile.TemporaryDirectory(prefix="xuanyue-safe-tool-") as directory:
        database = Path(directory) / "state.sqlite3"
        store = LocalStore(database)
        try:
            project = store.create_project("合成工具验收", directory)
            session = store.create_session(
                project["id"], "工具失败", kernel_class.id, "chosen"
            )
            run = store.start_run(session["id"], "检查合成数值。", "chosen")
            service = ChatService(store, Path(directory) / "unused.toml")
            kernel = kernel_class(
                ModelRouter({"chosen": ModelRoute("synthetic-upstream", model)}),
                tools,
                "Use the supplied synthetic tools, then explain their results.",
            )
            task = Task(run["id"], kernel_class.id, "chosen", "检查合成数值。")
            failure = None
            try:
                answer = await service._consume(
                    run["id"], Runtime([kernel]).stream(task)
                )
                service._finish_answer(run["id"], answer)
            except Exception as error:  # noqa: BLE001
                # 和产品线程边界一样只保存类别；异常正文留在测试进程内。
                failure = error
                store.fail_run(run["id"], type(error).__name__)
            saved = store.run(run["id"])
            with sqlite3.connect(database) as connection:
                database_dump = "\n".join(connection.iterdump())
            return model, saved, database_dump, failure
        finally:
            store.close()


class ToolErrorTests(unittest.IsolatedAsyncioTestCase):
    def assert_no_private_error(self, model, saved, database_dump) -> None:
        """序列化完整产品请求和持久化记录，不能只看最后一句答复。"""
        requests = json.dumps(
            [asdict(item) for item in model.requests], ensure_ascii=False
        )
        self.assertNotIn(_SECRET, requests)
        self.assertNotIn(_SECRET, json.dumps(saved, ensure_ascii=False))
        self.assertNotIn(_SECRET, database_dump)

    def assert_tool_results(self, model, saved, expected) -> None:
        self.assertEqual(len(model.requests), 2)
        self.assertTrue(
            all(item.model == "synthetic-upstream" for item in model.requests)
        )
        results = {
            part.id: part
            for message in model.requests[1].messages
            for part in message.parts
            if isinstance(part, ToolResult)
        }
        self.assertEqual(set(results), set(expected))
        ends = {
            item["payload"]["tool_call_id"]: item["payload"]
            for item in saved["events"]
            if item["kind"] == "tool_result_finished"
        }
        self.assertEqual(set(ends), set(expected))
        for call_id, (state, output) in expected.items():
            result = results[call_id]
            self.assertEqual(
                (result.name, result.state, result.output), (_SPEC.name, state, output)
            )
            self.assertEqual(ends[call_id]["state"], state)
            public_output = "".join(
                item["payload"]["delta"]
                for item in saved["events"]
                if item["kind"] == "tool_result_delta"
                and item["payload"]["tool_call_id"] == call_id
            )
            self.assertEqual(public_output, output)
        # 工具失败后模型可以作出完整解释；Run 完成不代表工具执行成功。
        self.assertEqual((saved["status"], saved["answer"]), ("completed", _ANSWER))

    async def test_unknown_tool_exception_is_safe_in_both_kernels(self) -> None:
        async def execute(_task, _arguments):
            raise RuntimeError(_SECRET)

        for kernel in _KERNELS:
            with self.subTest(kernel=kernel.id):
                model, saved, dump, failure = await _observe(
                    kernel,
                    LocalTools([ReadOnlyTool(_SPEC, _allow, execute)]),
                    (ToolCall("failed", _SPEC.name, '{"value": 1}'),),
                )
                self.assertIsNone(failure)
                self.assert_no_private_error(model, saved, dump)
                self.assert_tool_results(
                    model,
                    saved,
                    {"failed": ("error", safe_tool_failure("execution_failed").output)},
                )

    async def test_denied_authorization_never_executes_and_remains_error(self) -> None:
        executed = []

        async def deny(_task, _arguments):
            return False

        async def execute(_task, arguments):
            executed.append(arguments)
            return "should not execute"

        for kernel in _KERNELS:
            with self.subTest(kernel=kernel.id):
                model, saved, dump, failure = await _observe(
                    kernel,
                    LocalTools([ReadOnlyTool(_SPEC, deny, execute)]),
                    (ToolCall("denied", _SPEC.name, '{"value": 1}'),),
                )
                self.assertIsNone(failure)
                self.assert_no_private_error(model, saved, dump)
                self.assert_tool_results(
                    model,
                    saved,
                    {
                        "denied": (
                            "error",
                            safe_tool_failure("permission_denied").output,
                        )
                    },
                )
        self.assertEqual(executed, [])

    async def test_classified_business_failure_does_not_expose_chained_cause(
        self,
    ) -> None:
        async def execute(_task, _arguments):
            raise ToolFailure("resource_not_found") from RuntimeError(_SECRET)

        for kernel in _KERNELS:
            with self.subTest(kernel=kernel.id):
                model, saved, dump, failure = await _observe(
                    kernel,
                    LocalTools([ReadOnlyTool(_SPEC, _allow, execute)]),
                    (ToolCall("missing", _SPEC.name, '{"value": 1}'),),
                )
                self.assertIsNone(failure)
                self.assert_no_private_error(model, saved, dump)
                self.assert_tool_results(
                    model,
                    saved,
                    {
                        "missing": (
                            "error",
                            safe_tool_failure("resource_not_found").output,
                        )
                    },
                )

    async def test_non_text_result_is_rejected_without_stringifying_it(self) -> None:
        conversions = []

        class PrivateResult:
            def __str__(self):
                conversions.append("str")
                return _SECRET

            def __repr__(self):
                conversions.append("repr")
                return _SECRET

        async def execute(_task, _arguments):
            return PrivateResult()

        for kernel in _KERNELS:
            with self.subTest(kernel=kernel.id):
                model, saved, dump, failure = await _observe(
                    kernel,
                    LocalTools([ReadOnlyTool(_SPEC, _allow, execute)]),
                    (ToolCall("invalid", _SPEC.name, '{"value": 1}'),),
                )
                self.assertIsNone(failure)
                self.assert_no_private_error(model, saved, dump)
                self.assert_tool_results(
                    model,
                    saved,
                    {"invalid": ("error", safe_tool_failure("invalid_result").output)},
                )
        self.assertEqual(conversions, [])

    async def test_parallel_success_and_failure_keep_call_identity_and_state(
        self,
    ) -> None:
        succeeded = asyncio.Event()
        executed = []
        completion_order = []
        success_text = "error is a column name; this tool succeeded"

        async def execute(_task, arguments):
            executed.append(arguments["value"])
            if arguments["value"] == 1:
                # 让失败晚于成功返回，核对并行结果不会串到另一个 call ID。
                await asyncio.wait_for(succeeded.wait(), timeout=2)
                completion_order.append("failure")
                raise RuntimeError(_SECRET)
            completion_order.append("success")
            succeeded.set()
            return success_text

        for kernel in _KERNELS:
            with self.subTest(kernel=kernel.id):
                succeeded.clear()
                executed.clear()
                completion_order.clear()
                model, saved, dump, failure = await _observe(
                    kernel,
                    LocalTools([ReadOnlyTool(_SPEC, _allow, execute)]),
                    (
                        ToolCall("failed", _SPEC.name, '{"value": 1}'),
                        ToolCall("succeeded", _SPEC.name, '{"value": 2}'),
                    ),
                )
                self.assertIsNone(failure)
                self.assertCountEqual(executed, [1, 2])
                self.assertEqual(completion_order, ["success", "failure"])
                self.assert_no_private_error(model, saved, dump)
                self.assert_tool_results(
                    model,
                    saved,
                    {
                        "failed": (
                            "error",
                            safe_tool_failure("execution_failed").output,
                        ),
                        "succeeded": ("success", success_text),
                    },
                )

    async def test_invalid_model_arguments_fail_before_sdk_tool_dispatch(self) -> None:
        executed = []
        authorized = []

        async def authorize(_task, arguments):
            authorized.append(arguments)
            return True

        async def execute(_task, arguments):
            executed.append(arguments)
            return "should not execute"

        # 数字字符串也必须拒绝，防止 AgentScope 在产品校验前自行修正类型。
        invalid_arguments = (
            '{"value": "42"}',
            '{"value": 1',
            "[]",
            '{"value": NaN}',
            '{"value": Infinity}',
            '{"value": 1e999}',
            "{}",
            '{"value": true}',
            '{"value": 1, "extra": 2}',
        )
        for kernel in _KERNELS:
            for arguments in invalid_arguments:
                with self.subTest(kernel=kernel.id, arguments=arguments):
                    model, saved, dump, failure = await _observe(
                        kernel,
                        LocalTools([ReadOnlyTool(_SPEC, authorize, execute)]),
                        (ToolCall("bad-args", _SPEC.name, arguments),),
                    )
                    self.assertIsInstance(failure, ToolFailure)
                    self.assertEqual(failure.code, "invalid_arguments")
                    self.assertEqual(saved["status"], "failed")
                    self.assertEqual(len(model.requests), 1)
                    self.assertFalse(
                        any(
                            item["kind"] == "tool_result_finished"
                            for item in saved["events"]
                        )
                    )
                    self.assert_no_private_error(model, saved, dump)
        self.assertEqual((authorized, executed), ([], []))

    async def test_mixed_valid_and_invalid_calls_stop_the_entire_reply_before_dispatch(
        self,
    ) -> None:
        """同轮任一调用不合法就不调度整组，合法兄弟调用也不能提前执行。"""
        authorized = []
        executed = []

        async def authorize(_task, arguments):
            authorized.append(arguments)
            return True

        async def execute(_task, arguments):
            executed.append(arguments)
            return "should not execute"

        valid = ToolCall("valid-sibling", _SPEC.name, '{"value": 1}')
        invalid = ToolCall("invalid-sibling", _SPEC.name, '{"value": true}')
        for kernel in _KERNELS:
            for calls in ((valid, invalid), (invalid, valid)):
                with self.subTest(kernel=kernel.id, first_call=calls[0].id):
                    model, saved, dump, failure = await _observe(
                        kernel,
                        LocalTools([ReadOnlyTool(_SPEC, authorize, execute)]),
                        calls,
                    )
                    self.assertIsInstance(failure, ToolFailure)
                    self.assertEqual(failure.code, "invalid_arguments")
                    self.assertEqual(saved["status"], "failed")
                    self.assertEqual(len(model.requests), 1)
                    self.assertFalse(
                        any(
                            item["kind"].startswith("tool_result_")
                            for item in saved["events"]
                        )
                    )
                    self.assertFalse(
                        any(
                            isinstance(part, ToolResult)
                            for request in model.requests
                            for message in request.messages
                            for part in message.parts
                        )
                    )
                    self.assert_no_private_error(model, saved, dump)
        self.assertEqual((authorized, executed), ([], []))

    async def test_custom_service_errors_use_the_same_safe_boundary(self) -> None:
        class Service:
            def __init__(self, error):
                self.error = error

            def specs(self):
                return (_SPEC,)

            async def invoke(self, _task, _name, _arguments):
                raise self.error

        cases = (
            (ValidationError(_SECRET), "invalid_arguments"),
            (PermissionError(_SECRET), "permission_denied"),
            (RuntimeError(_SECRET), "execution_failed"),
        )
        for kernel in _KERNELS:
            for error, code in cases:
                with self.subTest(kernel=kernel.id, code=code):
                    model, saved, dump, failure = await _observe(
                        kernel,
                        Service(error),
                        (ToolCall("service-error", _SPEC.name, '{"value": 1}'),),
                    )
                    self.assertIsNone(failure)
                    self.assert_no_private_error(model, saved, dump)
                    self.assert_tool_results(
                        model,
                        saved,
                        {"service-error": ("error", safe_tool_failure(code).output)},
                    )

    async def test_missing_registration_has_fixed_failure_and_category(self) -> None:
        task = Task("missing", "agentscope", "chosen", "检查工具。")
        result = await execute_tool(LocalTools([]), task, "absent", {})
        self.assertEqual(result.error_code, "unavailable")
        self.assertEqual(result.state, "error")
        self.assertEqual(result.output, safe_tool_failure("unavailable").output)

    async def test_boundary_does_not_swallow_cancellation_or_base_exception(
        self,
    ) -> None:
        """只验收安全包装的传播语义，不宣称已经支持产品任务取消。"""

        class Service:
            def __init__(self, error):
                self.error = error

            async def invoke(self, _task, _name, _arguments):
                raise self.error

        task = Task("cancel", "agentscope", "chosen", "检查取消。")
        for error in (asyncio.CancelledError(), BaseException(_SECRET)):
            with self.subTest(error_type=type(error).__name__):
                with self.assertRaises(type(error)) as raised:
                    await execute_tool(Service(error), task, _SPEC.name, {"value": 1})
                self.assertIs(raised.exception, error)
