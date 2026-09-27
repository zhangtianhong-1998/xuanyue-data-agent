"""用本机 .env 做一次合成文字任务；只打印脱敏验收摘要。

这个脚本是开发验收入口，不保存供应商原始请求、回复或密钥。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from openai import AsyncOpenAI

from xuanyue import Event, Runtime, Task
from xuanyue.agentscope import AgentScopeKernel
from xuanyue.llm import ChatCompletionsClient, ModelRouter
from xuanyue.tools import LocalTools, ReadOnlyTool
from xuanyue.types import ToolSpec


def _settings() -> tuple[str, str, str]:
    """三项配置必须同源；这次验收只允许将密钥发送到 Coding Plan 网关。"""
    names = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
    environment = [os.environ.get(name) for name in names]
    if any(environment):
        values = environment
    else:
        local = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
        values = [local.get(name) for name in names]
    if any(not value for value in values):
        raise ValueError("set all three LLM settings from one source")
    key, base_url, model = values
    if base_url.rstrip("/") != "https://ark.cn-beijing.volces.com/api/coding/v3":
        raise ValueError("this smoke test requires the Coding Plan base URL")
    return key, base_url, model


async def _run() -> dict[str, object]:
    key, base_url, model = _settings()
    calls: list[dict[str, object]] = []
    spec = ToolSpec(
        "multiply",
        "Multiply a synthetic order count by units per order.",
        {
            "type": "object",
            "properties": {
                "orders": {"type": "integer"},
                "units_per_order": {"type": "integer"},
            },
            "required": ["orders", "units_per_order"],
            "additionalProperties": False,
        },
    )

    async def authorize(task: Task, arguments: dict[str, object]) -> bool:
        # 这次授权仅覆盖固定的合成输入，没有真实数据或外部副作用。
        return arguments == {"orders": 21, "units_per_order": 2}

    async def execute(task: Task, arguments: dict[str, object]) -> str:
        calls.append(dict(arguments))
        return str(arguments["orders"] * arguments["units_per_order"])

    async with AsyncOpenAI(
        api_key=key, base_url=base_url, timeout=45.0, max_retries=0
    ) as sdk:
        runtime = Runtime(
            [
                AgentScopeKernel(
                    ModelRouter({model: ChatCompletionsClient(sdk)}),
                    LocalTools([ReadOnlyTool(spec, authorize, execute)]),
                    "This is a synthetic test. Use the multiply tool before answering. "
                    "After the tool result, answer with the total only.",
                )
            ]
        )
        events = [
            event
            async for event in runtime.stream(
                Task(
                    "live-smoke-1",
                    "agentscope",
                    model,
                    "Synthetic data: 21 orders, 2 units per order. "
                    "Call multiply to verify the total, then answer briefly.",
                )
            )
        ]

    return _summarize(events, calls)


def _summarize(
    events: list[Event], calls: list[dict[str, object]]
) -> dict[str, object]:
    """独立核对工具、最终答复和正常终态，避免只看到部分事件就判成功。"""
    last_tool_seq = max(
        (event.seq for event in events if event.kind == "tool_result_finished"),
        default=0,
    )
    answer = "".join(
        str(event.payload.get("delta", ""))
        for event in events
        if event.kind == "text_delta" and event.seq > last_tool_seq
    )
    result = {
        "model_calls": sum(event.kind == "model_call_finished" for event in events),
        "executed_synthetic_tools": len(calls),
        "tool_result_succeeded": any(
            event.kind == "tool_result_finished"
            and event.payload.get("state") == "success"
            for event in events
        ),
        "final_answer_is_42": answer.strip() == "42",
        "reply_finished": any(
            event.kind == "reply_finished"
            and event.payload.get("finished_reason") == "completed"
            for event in events
        ),
        "coverage_gaps": sum(event.kind == "coverage_gap" for event in events),
        "usage": "unknown in product events",
    }
    result["ok"] = bool(
        result["model_calls"] == 2
        and result["executed_synthetic_tools"] == 1
        and result["tool_result_succeeded"]
        and result["final_answer_is_42"]
        and result["reply_finished"]
        and result["coverage_gaps"] == 0
    )
    return result


if __name__ == "__main__":
    try:
        summary = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - SDK 正文可能含敏感请求，统一脱敏输出。
        # SDK 错误正文可能包含请求信息；只输出类别和 HTTP 状态码。
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(0 if summary["ok"] else 1)
