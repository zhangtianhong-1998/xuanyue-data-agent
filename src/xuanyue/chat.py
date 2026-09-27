"""把持久化会话中的一轮文字或图文请求交给用户选定的 Agent 内核。

HTTP 请求只预留 Run 并启动后台线程；线程按事件顺序先落盘，再供界面读取。
下一轮只带入已完成的公开问答，工具轨迹用于展示而非跨框架重放。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlsplit

from xuanyue.config import (
    ConfigurationError,
    ModelSettings,
    list_model_settings,
    load_api_key,
    load_model_settings,
)
from xuanyue.engines import EngineRegistry, default_engine_registry
from xuanyue.interfaces import ModelClient
from xuanyue.llm import ChatCompletionsClient, ModelRoute, ModelRouter
from xuanyue.runtime import Runtime
from xuanyue.storage import ImageInputNotSupported, LocalStore
from xuanyue.tools import LocalTools, multiply_demo_tool
from xuanyue.types import Event, Message, ModelReply, ModelRequest, Task, Text

_SYSTEM_PROMPT = (
    "You are a careful, concise assistant. Reply in the user's language. "
    "Use the available multiply tool when it helps verify arithmetic. "
    "Do not claim to have accessed data or tools that you did not use."
)
_TITLE_PROMPT = (
    "请根据第一轮用户请求和助手答复，为这段会话生成一个简短标题。"
    "使用用户的语言，只输出标题，不要解释、编号或引号；不要超过 60 个字符。"
)
_LOG = logging.getLogger(__name__)


class ModelNotConfigured(RuntimeError):
    """当前本机尚不能按会话的模型绑定发起真实调用。"""


def _tools() -> LocalTools:
    """本切片仅复用纯计算示例工具；数据分析工具尚未进入产品。"""
    return LocalTools([multiply_demo_tool()])


def _generated_title(reply: ModelReply) -> str | None:
    """只接受短的单行文字；供应商的整段解释不会进入项目侧栏。"""
    if len(reply.parts) != 1 or not isinstance(reply.parts[0], Text):
        return None
    title = reply.parts[0].value.strip()
    if "\n" in title or "\r" in title:
        return None
    title = re.sub(
        r"^(?:会话标题|标题|Title)\s*[:：]\s*", "", title, flags=re.IGNORECASE
    )
    title = title.strip(" \t`\"'“”‘’#*")
    if not title or len(title) > 60 or any(ord(char) < 32 for char in title):
        return None
    return title


class ChatService:
    """为本机 HTTP 界面提供会话操作；不会自动替换已选内核。"""

    def __init__(
        self,
        store: LocalStore,
        config_path: str | Path,
        registry: EngineRegistry | None = None,
        run_stream: Callable[[Task], AsyncIterator[Event]] | None = None,
    ) -> None:
        self.store = store
        self.config_path = Path(config_path)
        self.registry = registry if registry is not None else default_engine_registry()
        # 注入运行流仅用于验收持久化与 HTTP 边界；正常路径创建真实模型客户端。
        self._run_stream = run_stream

    def _settings_status(self, settings: ModelSettings) -> dict[str, object]:
        """只向界面暴露模型 ID、可用状态和目标域名，不返回地址或密钥来源。"""
        result = {
            "id": settings.product_model_id,
            "configured": False,
            "destination": urlsplit(settings.base_url).hostname,
            "image_input": settings.image_input,
        }
        try:
            load_api_key(self.config_path, settings.api_key_env)
        except (ConfigurationError, ImportError):
            return result
        if importlib.util.find_spec("openai") is None:
            return result
        result["configured"] = True
        return result

    def model_status(self, model_id: str | None = None) -> dict[str, object]:
        """按保存的产品模型 ID 查询；未登记的旧模型不能回退到默认项。"""
        try:
            settings = load_model_settings(self.config_path, model_id)
        except ConfigurationError:
            return {
                "id": model_id,
                "configured": False,
                "destination": None,
                "image_input": False,
            }
        return self._settings_status(settings)

    def model_catalog(self) -> list[dict[str, object]]:
        """列出本机登记的模型；配置无效时仍允许界面查看旧会话。"""
        try:
            settings = list_model_settings(self.config_path)
        except ConfigurationError:
            return []
        return [self._settings_status(item) for item in settings]

    def bootstrap(self) -> dict[str, object]:
        return {
            "user": self.store.user(),
            "projects": self.store.projects(),
            "kernels": list(self.registry.names),
            "model": self.model_status(),
            "models": self.model_catalog(),
        }

    def session_detail(self, session_id: str) -> dict[str, object]:
        """会话模型可能不是当前默认项，界面必须展示本会话的真实发送目标。"""
        detail = self.store.session_detail(session_id)
        detail["model_status"] = self.model_status(detail["session"]["model"])
        return detail

    def create_session(
        self,
        project_id: str,
        title: str | None,
        kernel: str,
        model_id: str | None = None,
    ) -> dict[str, object]:
        """建会话时固定主内核和模型；省略标题才由首轮模型自动命名。"""
        if kernel not in self.registry.names:
            raise ValueError("unknown kernel")
        try:
            model = load_model_settings(self.config_path, model_id).product_model_id
        except ConfigurationError:
            if model_id is not None:
                raise ValueError("requested model is not registered") from None
            model = None
        return self.store.create_session(
            project_id,
            title if title is not None else "新会话",
            kernel,
            model,
            auto_title=title is None,
        )

    def _model_binding(self, model_id: str | None) -> tuple[ModelSettings, str]:
        try:
            settings = load_model_settings(self.config_path, model_id)
            key = load_api_key(self.config_path, settings.api_key_env)
        except (ConfigurationError, ImportError) as exc:
            raise ModelNotConfigured("model configuration is unavailable") from exc
        if settings.protocol != "openai_chat_completions":
            raise ModelNotConfigured("model protocol has no client")
        if importlib.util.find_spec("openai") is None:
            raise ModelNotConfigured("model client is not installed")
        return settings, key

    def start_turn(
        self, session_id: str, text: str, attachment_ids: tuple[str, ...] = ()
    ) -> str:
        """先确认模型绑定，再原子创建 Run；在途 Run 阻止同会话并发提交。"""
        session = self.store.session(session_id)
        settings, key = self._model_binding(session["model"])
        if attachment_ids and not settings.image_input:
            raise ImageInputNotSupported("selected model does not accept images")
        run = self.store.start_run(
            session_id,
            text,
            settings.product_model_id,
            attachment_ids,
            allow_images=settings.image_input,
        )
        try:
            task = Task(
                run["id"],
                run["kernel"],
                run["model"],
                text,
                history=self.store.history(session_id, run["id"]),
                images=self.store.images_for_run(run["id"]),
            )
            worker = threading.Thread(
                target=self._execute_thread,
                args=(task, settings, key),
                name=f"xuanyue-run-{run['id'][:8]}",
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            self.store.fail_run(run["id"], type(exc).__name__)
            raise
        return run["id"]

    def _execute_thread(self, task: Task, settings: ModelSettings, key: str) -> None:
        try:
            asyncio.run(self._execute(task, settings, key))
        except Exception as exc:  # noqa: BLE001
            # 供应商错误可能包含用户内容或凭据；持久化的只有异常类别。
            try:
                self.store.fail_run(task.run_id, type(exc).__name__)
            except ValueError:
                # 进程恢复若已将旧 Run 标为 interrupted，不覆盖该终态。
                pass

    async def _execute(self, task: Task, settings: ModelSettings, key: str) -> None:
        if self._run_stream is not None:
            stream = self._run_stream(task)
            answer = await self._consume(task.run_id, stream)
            self._finish_answer(task.run_id, answer)
            return

        from openai import AsyncOpenAI

        async with AsyncOpenAI(
            api_key=key, base_url=settings.base_url, timeout=45.0, max_retries=0
        ) as sdk:
            client: ModelClient = ChatCompletionsClient(sdk)
            router = ModelRouter(
                {task.model: ModelRoute(settings.upstream_model, client)}
            )
            kernel = self.registry.create(task.kernel, router, _tools(), _SYSTEM_PROMPT)
            answer = await self._consume(task.run_id, Runtime([kernel]).stream(task))
            self._finish_answer(task.run_id, answer)
            if answer is not None:
                # 标题是独立的附加请求：Run 的终态、耗时和公开轨迹只反映主答复。
                try:
                    if self.store.claim_first_title(task.run_id):
                        title = None
                        try:
                            title = await self._generate_first_title(
                                router, task, answer
                            )
                        except Exception as exc:  # noqa: BLE001
                            _LOG.warning(
                                "session title generation failed: %s",
                                type(exc).__name__,
                            )
                        self.store.finish_first_title(task.run_id, title)
                except Exception as exc:  # noqa: BLE001
                    # 元数据写入故障不可反过来把已完成的问答标记为失败。
                    _LOG.warning("session title update failed: %s", type(exc).__name__)

    async def _generate_first_title(
        self, client: ModelClient, task: Task, answer: str
    ) -> str | None:
        """复用本会话模型绑定；不向内核注入标题请求，也不记录隐藏推理。"""
        request = ModelRequest(
            task.model,
            (
                Message("system", (Text(_TITLE_PROMPT),)),
                Message(
                    "user",
                    (
                        Text(
                            f"用户首轮请求：\n{task.text[:600]}\n\n"
                            f"助手答复：\n{answer[:600]}"
                        ),
                    ),
                ),
            ),
            (),
        )
        # 完整答复已经流式送达；标题请求最多再占用八秒，避免卡住终态。
        reply = await asyncio.wait_for(client.complete(request), timeout=8.0)
        return _generated_title(reply)

    def _finish_answer(self, run_id: str, answer: str | None) -> None:
        if answer is not None:
            self.store.finish_run(run_id, "completed", answer, None)
        else:
            self.store.finish_run(run_id, "incomplete", None, None)

    async def _consume(self, run_id: str, stream: AsyncIterator[Event]) -> str | None:
        """记录所有公开事件后提取完整答案；开始事件不能算完成。"""
        answer_fragments: list[str] = []
        completed = False
        async for event in stream:
            self.store.append_event(run_id, event)
            if event.kind == "tool_result_finished":
                answer_fragments.clear()
            elif event.kind == "text_delta":
                delta = event.payload.get("delta")
                if isinstance(delta, str):
                    answer_fragments.append(delta)
            elif event.kind == "reply_finished":
                completed = event.payload.get("finished_reason") == "completed"
        answer = "".join(answer_fragments).strip()
        if completed and answer:
            return answer
        return None
