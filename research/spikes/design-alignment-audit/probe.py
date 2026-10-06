"""用合成输入复核设计审查发现；不读取本机配置，也不调用网络模型。

直接运行真实框架适配器，使用临时 SQLite 保存事件和终态。输出是当前行为
的观察记录，不把有缺陷的行为当作应该保持的测试答案。
"""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
import tempfile
from importlib.metadata import version
from pathlib import Path

from xuanyue.chat import ChatService
from xuanyue.config import load_model_settings
from xuanyue.engines.agentscope import AgentScopeKernel
from xuanyue.engines.langgraph import LangGraphKernel
from xuanyue.llm import ModelRoute, ModelRouter
from xuanyue.model_config_editor import replace_model_config
from xuanyue.storage import LocalStore
from xuanyue.tools import LocalTools, ReadOnlyTool, multiply_demo_tool
from xuanyue.types import (
    Message,
    ModelReply,
    Task,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
)

KERNELS = (AgentScopeKernel, LangGraphKernel)
SYNTHETIC_ERROR = "SYNTHETIC_SECRET_MARKER_DO_NOT_EXPOSE"


class ScriptedModel:
    """只返回固定内容并保留产品请求；ModelRouter 提供非流式测试桥。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.requests = []

    async def complete(self, request) -> ModelReply:
        self.requests.append(request)
        count = len(self.requests)
        if self.mode == "tool_error" and count == 1:
            return ModelReply((ToolCall("call-1", "synthetic_read", "{}"),))
        if self.mode == "loop" and request.tool_mode != "none":
            return ModelReply(
                (
                    ToolCall(
                        f"call-{count}", "multiply", '{"orders":2,"units_per_order":3}'
                    ),
                )
            )
        return ModelReply((Text("Synthetic completed reply"),))


async def observe_run(kernel_class, model, tools, history=()) -> dict:
    """复用产品事件消费与终态保存；临时库不会影响已有项目或会话。"""
    with tempfile.TemporaryDirectory(prefix="xuanyue-design-audit-") as directory:
        store = LocalStore(Path(directory) / "audit.sqlite3")
        try:
            project = store.create_project("synthetic", directory)
            session = store.create_session(
                project["id"], "synthetic", kernel_class.id, "m"
            )
            run = store.start_run(session["id"], "Synthetic question", "m")
            service = ChatService(store, Path(directory) / "unused.toml")
            kernel = kernel_class(
                ModelRouter({"m": ModelRoute("synthetic-upstream", model)}),
                tools,
                "Use the registered tools when asked.",
            )
            task = Task(
                run["id"], kernel_class.id, "m", "Latest question", history=history
            )
            exception_type = None
            try:
                answer = await service._consume(run["id"], kernel.stream(task))
                service._finish_answer(run["id"], answer)
            except Exception as exc:  # noqa: BLE001
                # 审查必须记录框架实际失败类别，不能把失败变成成功记录。
                exception_type = type(exc).__name__
                store.fail_run(run["id"], exception_type)
            saved = store.run(run["id"])
            return {
                "kernel": kernel_class.id,
                "status": saved["status"],
                "exception_type": exception_type,
                "events": [
                    {"kind": event["kind"], "payload": event["payload"]}
                    for event in saved["events"]
                ],
            }
        finally:
            store.close()


async def history_probe() -> list[dict]:
    # 每条问题约 19,021 字符，小于 HTTP 的 20,000 字符限制；共八轮。
    history = tuple(
        message
        for index in range(8)
        for message in (
            Message("user", (Text(f"SYNTHETIC_HISTORY_{index}: " + "data " * 3800),)),
            Message("assistant", (Text("noted"),)),
        )
    )
    observations = []
    for kernel_class in KERNELS:
        model = ScriptedModel("history")
        result = await observe_run(kernel_class, model, LocalTools([]), history)
        text = " ".join(
            part.value
            for request in model.requests
            for message in request.messages
            for part in message.parts
            if isinstance(part, Text)
        )
        result.update(
            actual_model_requests=len(model.requests),
            input_history_pairs=8,
            history_markers_received=[
                index for index in range(8) if f"SYNTHETIC_HISTORY_{index}:" in text
            ],
            truncation_notice_to_model="truncated" in text,
        )
        observations.append(result)
    return observations


async def tool_error_probe() -> list[dict]:
    async def authorize(_task, _arguments):
        return True

    async def execute(_task, _arguments):
        raise RuntimeError(SYNTHETIC_ERROR)

    spec = ToolSpec(
        "synthetic_read",
        "Synthetic read failure",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    observations = []
    for kernel_class in KERNELS:
        model = ScriptedModel("tool_error")
        result = await observe_run(
            kernel_class, model, LocalTools([ReadOnlyTool(spec, authorize, execute)])
        )
        result.update(
            raw_error_in_saved_public_event=any(
                SYNTHETIC_ERROR in str(event["payload"]) for event in result["events"]
            ),
            raw_error_in_next_model_request=any(
                isinstance(part, ToolResult) and SYNTHETIC_ERROR in part.output
                for request in model.requests
                for message in request.messages
                for part in message.parts
            ),
        )
        observations.append(result)
    return observations


async def iteration_probe() -> list[dict]:
    observations = []
    for kernel_class in KERNELS:
        model = ScriptedModel("loop")
        calls = []
        tool = multiply_demo_tool(
            on_execute=lambda arguments, record=calls: record.append(dict(arguments))
        )
        result = await observe_run(kernel_class, model, LocalTools([tool]))
        result.update(
            actual_model_requests=len(model.requests),
            actual_tool_executions=len(calls),
            model_start_events=sum(
                event["kind"] == "model_call_started" for event in result["events"]
            ),
            model_end_events=sum(
                event["kind"] == "model_call_finished" for event in result["events"]
            ),
        )
        observations.append(result)
    return observations


def binding_probe() -> dict:
    """只修改临时目录的合成模型目录；不建立网络连接或加载真实凭据。"""
    with tempfile.TemporaryDirectory(prefix="xuanyue-binding-audit-") as directory:
        config = Path(directory) / "synthetic.toml"
        catalog = {
            "default_model": "m",
            "providers": [
                {
                    "id": "p",
                    "protocol": "openai_chat_completions",
                    "base_url": "https://first.example.invalid/v1",
                }
            ],
            "models": [
                {
                    "id": "m",
                    "provider": "p",
                    "upstream_model": "upstream-first",
                    "image_input": False,
                }
            ],
        }
        replace_model_config(config, catalog)
        store = LocalStore(Path(directory) / "audit.sqlite3")
        try:
            project = store.create_project("synthetic", directory)
            session = store.create_session(
                project["id"], "synthetic", "agentscope", "m"
            )
            run = store.start_run(session["id"], "question", "m")
            store.finish_run(run["id"], "completed", "answer", None)
            before = load_model_settings(config, "m")
            catalog["providers"][0]["base_url"] = "https://second.example.invalid/v1"
            catalog["models"][0]["upstream_model"] = "upstream-second"
            replace_model_config(config, catalog, store.models_in_use())
            after = load_model_settings(config, "m")
            return {
                "same_product_model_id": before.product_model_id
                == after.product_model_id,
                "change_of_referenced_destination_accepted": before.base_url
                != after.base_url,
                "change_of_referenced_upstream_model_accepted": before.upstream_model
                != after.upstream_model,
                "saved_run_fields": sorted(store.run(run["id"]).keys()),
                "network_request_performed": False,
            }
        finally:
            store.close()


async def main() -> None:
    root = Path(__file__).resolve().parents[3]
    results = {
        "review_date": "2026-10-03",
        "code_revision": (
            await asyncio.to_thread(
                subprocess.check_output,
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                text=True,
            )
        ).strip(),
        "python": platform.python_version(),
        "packages": {
            name: version(name)
            for name in (
                "agentscope",
                "langchain",
                "langgraph",
                "langchain-core",
                "openai",
            )
        },
        "network_models_used": False,
        "long_history": await history_probe(),
        "tool_exception": await tool_error_probe(),
        "iteration_limit": await iteration_probe(),
        "mutable_model_binding": binding_probe(),
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
