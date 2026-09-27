"""用同一 Task 输入比较两种主内核；默认合成模式不访问网络。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from xuanyue import Event, ModelClient, Runtime, Task
from xuanyue.llm import ChatCompletionsClient, ModelRoute, ModelRouter
from xuanyue.tools import LocalTools, ReadOnlyTool
from xuanyue.types import ModelReply, ModelRequest, Text, ToolCall, ToolResult, ToolSpec

_SYNTHETIC_TEXT = "合成数据：21 单，每单 2 件。请用 multiply 核对总件数，只回答数字。"
_SYSTEM_PROMPT = (
    "You are a concise assistant. For the synthetic order-count task, use the "
    "multiply tool before answering. For other tasks, use it only when needed."
)
_CODING_PLAN_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"


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


def _live_settings() -> tuple[str, str, str]:
    """密钥、地址、模型名必须来自同一来源；当前仅接既有 Coding Plan 配置。"""
    from dotenv import dotenv_values

    names = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
    if any(name in os.environ for name in names):
        values = [os.environ.get(name) for name in names]
    else:
        local = dotenv_values(Path.cwd() / ".env")
        values = [local.get(name) for name in names]
    if any(not value for value in values):
        raise ValueError("all three LLM settings must come from one source")
    key, base_url, model = values
    if base_url.rstrip("/") != _CODING_PLAN_URL:
        raise ValueError("live CLI currently requires the Coding Plan base URL")
    return key, base_url, model


def _kernel(kernel_id: str, model: ModelClient, tools: LocalTools):
    """只改变主内核，模型、工具、系统提示词和 Task 结构保持相同。"""
    if kernel_id == "agentscope":
        from xuanyue.agentscope import AgentScopeKernel

        return AgentScopeKernel(model, tools, _SYSTEM_PROMPT)
    if kernel_id == "langgraph":
        from xuanyue.langgraph import LangGraphKernel

        return LangGraphKernel(model, tools, _SYSTEM_PROMPT)
    raise ValueError("unknown kernel")


async def _execute(
    kernel_id: str,
    mode: str,
    text: str,
    model_id: str,
    client: ModelClient,
    show_events: bool,
) -> dict[str, object]:
    """两个内核都从同一个产品 Task 进入 Runtime。"""
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
    router = ModelRouter({model_id: ModelRoute(model_id, client)})
    runtime = Runtime([_kernel(kernel_id, router, tools)])
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


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.mode == "synthetic":
        if args.text is not None:
            raise ValueError("custom --text requires --mode live")
        return await _execute(
            args.kernel,
            "synthetic",
            _SYNTHETIC_TEXT,
            "synthetic",
            _SyntheticModel(),
            args.events,
        )
    if not args.allow_remote:
        raise ValueError("--mode live requires --allow-remote")
    key, base_url, model_id = _live_settings()
    from openai import AsyncOpenAI

    async with AsyncOpenAI(
        api_key=key, base_url=base_url, timeout=45.0, max_retries=0
    ) as sdk:
        return await _execute(
            args.kernel,
            "live",
            args.text if args.text is not None else _SYNTHETIC_TEXT,
            model_id,
            ChatCompletionsClient(sdk),
            args.events,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="选择 AgentScope 或 LangGraph，运行一个文字任务。"
    )
    parser.add_argument("--kernel", choices=("agentscope", "langgraph"), required=True)
    parser.add_argument("--mode", choices=("synthetic", "live"), default="synthetic")
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
        result = asyncio.run(_run(args))
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
