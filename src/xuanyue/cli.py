"""用同一 Task 输入比较两种主内核；默认合成模式不访问网络。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import uuid4

from xuanyue import Event, ModelClient, Runtime, Task
from xuanyue.config import ConfigurationError, load_api_key, load_model_settings
from xuanyue.engines import EngineNameConflict, EngineRegistry, default_engine_registry
from xuanyue.llm import ChatCompletionsClient, ModelRoute, ModelRouter
from xuanyue.tools import LocalTools, ReadOnlyTool
from xuanyue.types import ModelReply, ModelRequest, Text, ToolCall, ToolResult, ToolSpec

_SYNTHETIC_TEXT = "合成数据：21 单，每单 2 件。请用 multiply 核对总件数，只回答数字。"
_SYSTEM_PROMPT = (
    "You are a concise assistant. For the synthetic order-count task, use the "
    "multiply tool before answering. For other tasks, use it only when needed."
)


class _SyntheticModel(ModelClient):
    """固定要求一次工具往返，用于无密钥验证两个内核的消息转换。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            if not any(spec.name == "multiply" for spec in request.tools):
                raise ValueError("synthetic task did not receive multiply")
            return ModelReply(
                (
                    ToolCall(
                        "synthetic-call-1",
                        "multiply",
                        '{"orders":21,"units_per_order":2}',
                    ),
                )
            )
        results = [
            part
            for message in request.messages
            for part in message.parts
            if isinstance(part, ToolResult)
        ]
        if self.calls != 2 or not any(
            result.id == "synthetic-call-1"
            and result.output == "42"
            and result.state == "success"
            for result in results
        ):
            raise ValueError("synthetic tool result was not returned to the model")
        return ModelReply((Text("42"),))


async def _execute(
    registry: EngineRegistry,
    kernel_id: str,
    mode: str,
    text: str,
    model_id: str,
    upstream_model: str,
    client: ModelClient,
    show_events: bool,
) -> dict[str, object]:
    """选定引擎从同一个产品 Task 进入 Runtime。"""
    executed: list[dict[str, object]] = []
    spec = ToolSpec(
        "multiply",
        "Multiply a synthetic order count by units per order.",
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

    async def authorize(task: Task, arguments: dict[str, object]) -> bool:
        # 合成模式只准固定输入；实时模式的纯计算工具仍受上方 schema 限制。
        return mode == "live" or arguments == {"orders": 21, "units_per_order": 2}

    async def multiply(task: Task, arguments: dict[str, object]) -> str:
        executed.append(dict(arguments))
        return str(arguments["orders"] * arguments["units_per_order"])

    tools = LocalTools([ReadOnlyTool(spec, authorize, multiply)])
    router = ModelRouter({model_id: ModelRoute(upstream_model, client)})
    runtime = Runtime([registry.create(kernel_id, router, tools, _SYSTEM_PROMPT)])
    task = Task(f"cli-{uuid4().hex[:12]}", kernel_id, model_id, text)
    events: list[Event] = []
    async for event in runtime.stream(task):
        events.append(event)
        if show_events:
            print(
                json.dumps(
                    {
                        "type": "event",
                        "run_id": event.run_id,
                        "seq": event.seq,
                        "kind": event.kind,
                        "payload": event.payload,
                    },
                    ensure_ascii=False,
                )
            )
    last_tool_seq = max(
        (event.seq for event in events if event.kind == "tool_result_finished"),
        default=0,
    )
    answer = "".join(
        str(event.payload.get("delta", ""))
        for event in events
        if event.kind == "text_delta" and event.seq > last_tool_seq
    ).strip()
    completed = any(
        event.kind == "reply_finished"
        and event.payload.get("finished_reason") == "completed"
        for event in events
    )
    result: dict[str, object] = {
        "type": "summary",
        "run_id": task.run_id,
        "kernel": task.kernel,
        "model": task.model,
        "mode": mode,
        "status": "completed" if completed else "incomplete",
        "answer": answer,
        "model_calls": sum(event.kind == "model_call_finished" for event in events),
        "tool_executions": len(executed),
        "coverage_gaps": sum(event.kind == "coverage_gap" for event in events),
    }
    if mode == "synthetic" and not (
        completed
        and answer == "42"
        and result["model_calls"] == 2
        and len(executed) == 1
        and result["coverage_gaps"] == 0
    ):
        result["status"] = "failed_acceptance"
    return result


async def _run(args: argparse.Namespace, registry: EngineRegistry) -> dict[str, object]:
    if args.mode == "synthetic":
        if args.text is not None or args.model is not None:
            raise ValueError("custom --text and --model require --mode live")
        return await _execute(
            registry,
            args.kernel,
            "synthetic",
            _SYNTHETIC_TEXT,
            "synthetic",
            "synthetic",
            _SyntheticModel(),
            args.events,
        )
    if not args.allow_remote:
        raise ValueError("--mode live requires --allow-remote")
    settings = load_model_settings(args.config, args.model)
    if settings.protocol != "openai_chat_completions":
        # 增加配置枚举时仍须先接入对应客户端，不能错发到现有协议。
        raise ConfigurationError("configured protocol has no CLI client")
    key = load_api_key(args.config, settings.api_key_env)
    from openai import AsyncOpenAI

    async with AsyncOpenAI(
        api_key=key, base_url=settings.base_url, timeout=45.0, max_retries=0
    ) as sdk:
        return await _execute(
            registry,
            args.kernel,
            "live",
            args.text if args.text is not None else _SYNTHETIC_TEXT,
            settings.product_model_id,
            settings.upstream_model,
            ChatCompletionsClient(sdk),
            args.events,
        )


def main() -> int:
    try:
        registry = default_engine_registry()
    except Exception as exc:  # noqa: BLE001
        # 已安装的扩展若登记冲突，也只报告异常类型，不打印扩展错误正文。
        failure: dict[str, str] = {
            "type": "summary",
            "status": "failed",
            "error_type": type(exc).__name__,
        }
        if isinstance(exc, EngineNameConflict):
            failure["engine"] = exc.name
        print(
            json.dumps(failure),
            file=sys.stderr,
        )
        return 1
    parser = argparse.ArgumentParser(
        description="选择已登记的 Agent 引擎，运行一个文字任务。"
    )
    parser.add_argument("--kernel", choices=registry.names, required=True)
    parser.add_argument("--mode", choices=("synthetic", "live"), default="synthetic")
    parser.add_argument(
        "--config", default="xuanyue.toml", help="真实模型配置文件路径。"
    )
    parser.add_argument(
        "--model", help="配置文件中的产品模型 ID；默认用 default_model。"
    )
    parser.add_argument(
        "--text",
        help="真实模型模式下的任务文字；默认使用合成订单问题。",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="允许真实模型模式调用已配置的远端服务。",
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help="在汇总前逐行打印公开事件 JSON。",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args, registry))
    except Exception as exc:  # noqa: BLE001
        # 原始供应商异常可能含请求正文或密钥，只输出异常类别。
        print(
            json.dumps(
                {
                    "type": "summary",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
