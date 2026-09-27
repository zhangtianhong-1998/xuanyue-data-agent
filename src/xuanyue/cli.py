"""交互式文字会话；固定合成题仅供显式的离线内核验收。"""

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
from xuanyue.tools import LocalTools, multiply_demo_tool
from xuanyue.types import (
    Message,
    ModelReply,
    ModelRequest,
    Text,
    ToolCall,
    ToolResult,
)

_SYNTHETIC_TEXT = "合成数据：21 单，每单 2 件。请用 multiply 核对总件数，只回答数字。"
_SYSTEM_PROMPT = (
    "You are a careful, concise assistant. Reply in the user's language. "
    "Use the available multiply tool when it helps verify arithmetic. "
    "Do not claim to have accessed data or tools that you did not use."
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
    history: tuple[Message, ...] = (),
) -> dict[str, object]:
    """一次输入形成独立 Run；历史由会话管理者传入，不由内核私存。"""
    executed: list[dict[str, object]] = []
    tools = LocalTools(
        [
            multiply_demo_tool(
                allowed_input=(
                    {"orders": 21, "units_per_order": 2}
                    if mode == "synthetic"
                    else None
                ),
                on_execute=lambda arguments: executed.append(dict(arguments)),
            )
        ]
    )
    router = ModelRouter({model_id: ModelRoute(upstream_model, client)})
    runtime = Runtime([registry.create(kernel_id, router, tools, _SYSTEM_PROMPT)])
    task = Task(f"cli-{uuid4().hex[:12]}", kernel_id, model_id, text, history)
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
        "status": "completed" if completed and answer else "incomplete",
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


async def _chat(
    registry: EngineRegistry,
    kernel_id: str,
    model_id: str,
    upstream_model: str,
    client: ModelClient,
    show_events: bool,
) -> None:
    """只保存本进程已完成的文字问答；失败轮次不进入下一轮上下文。"""
    history: tuple[Message, ...] = ()
    print(f"玄月 CLI · {kernel_id} · 模型 {model_id}")
    print("输入需求并回车；本轮文字会发送到配置的模型服务。/exit 或 /quit 退出。")
    while True:
        try:
            text = input("你> " if sys.stdin.isatty() else "").strip()
        except (EOFError, KeyboardInterrupt):
            if sys.stdin.isatty():
                print()
            return
        if text in ("/exit", "/quit"):
            return
        if not text:
            continue
        try:
            result = await _execute(
                registry,
                kernel_id,
                "chat",
                text,
                model_id,
                upstream_model,
                client,
                show_events,
                history=history,
            )
        except Exception as exc:  # noqa: BLE001
            # 服务端报错可能含用户输入或凭据，交互会话仅展示异常类别。
            print(
                json.dumps({"type": "turn_error", "error_type": type(exc).__name__}),
                file=sys.stderr,
            )
            continue
        answer = result["answer"]
        if (
            result["status"] != "completed"
            or not isinstance(answer, str)
            or not answer.strip()
        ):
            print("本轮未完成，未加入对话历史。", file=sys.stderr)
            continue
        print(f"助手> {answer}")
        history += (
            Message("user", (Text(text),)),
            Message("assistant", (Text(answer),)),
        )


async def _run(
    args: argparse.Namespace, registry: EngineRegistry
) -> dict[str, object] | None:
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
    if args.mode == "live" and not args.allow_remote:
        raise ValueError("--mode live requires --allow-remote")
    if args.mode == "chat" and args.text is not None:
        raise ValueError("--text requires --mode live")
    if args.mode == "chat" and not sys.stdin.isatty() and not args.allow_remote:
        raise ValueError("piped chat input requires --allow-remote")
    settings = load_model_settings(args.config, args.model)
    if settings.protocol != "openai_chat_completions":
        # 增加配置枚举时仍须先接入对应客户端，不能错发到现有协议。
        raise ConfigurationError("configured protocol has no CLI client")
    key = load_api_key(args.config, settings.api_key_env)
    from openai import AsyncOpenAI

    async with AsyncOpenAI(
        api_key=key, base_url=settings.base_url, timeout=45.0, max_retries=0
    ) as sdk:
        client = ChatCompletionsClient(sdk)
        if args.mode == "chat":
            await _chat(
                registry,
                args.kernel,
                settings.product_model_id,
                settings.upstream_model,
                client,
                args.events,
            )
            return None
        return await _execute(
            registry,
            args.kernel,
            "live",
            args.text if args.text is not None else _SYNTHETIC_TEXT,
            settings.product_model_id,
            settings.upstream_model,
            client,
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
    parser = argparse.ArgumentParser(description="选择 Agent 引擎，连续输入文字需求。")
    parser.add_argument("--kernel", choices=registry.names, required=True)
    parser.add_argument("--mode", choices=("chat", "synthetic", "live"), default="chat")
    parser.add_argument(
        "--config", default="xuanyue.toml", help="真实模型配置文件路径。"
    )
    parser.add_argument(
        "--model", help="配置文件中的产品模型 ID；默认用 default_model。"
    )
    parser.add_argument(
        "--text",
        help="--mode live 的单次任务文字；不传则运行原有的合成订单问题。",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="允许单次真实模型调用，或从管道向交互会话发送文字。",
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help="在汇总前逐行打印公开事件 JSON。",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args, registry))
    except KeyboardInterrupt:
        # 网络调用中按 Ctrl-C 时也应安静结束，不展示堆栈或请求内容。
        print("\n已中断。", file=sys.stderr)
        return 130
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
    if result is None:
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
