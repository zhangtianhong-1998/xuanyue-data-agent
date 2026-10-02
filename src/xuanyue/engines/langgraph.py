"""把产品任务接入 LangChain 的 LangGraph Agent；Agent 循环仍由框架运行。

当前转换文字、用户图片、函数工具与公开文字流。产品继续掌管模型选择
与工具授权；LangGraph 的检查点及其他模态尚未接入。
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from xuanyue.interfaces import AgentKernel, ModelClient, ToolService
from xuanyue.types import (
    Event,
    Image,
    Message,
    ModelReply,
    ModelRequest,
    Task,
    Text,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class UnsupportedLangGraphContent(ValueError):
    """框架消息或选项超出当前产品消息的可表示范围。"""


def _text(content: object) -> str:
    if not isinstance(content, str):
        raise UnsupportedLangGraphContent("only text message content is supported")
    return content


def _native_user_content(parts: tuple[Text | Image, ...]) -> str | list[dict[str, str]]:
    """将文字与本地图片按原顺序交给 LangChain 标准内容块。"""
    if all(isinstance(part, Text) for part in parts):
        return "".join(part.value for part in parts)
    converted: list[dict[str, str]] = []
    for part in parts:
        if isinstance(part, Text):
            converted.append({"type": "text", "text": part.value})
        elif isinstance(part, Image):
            converted.append(
                {
                    "type": "image",
                    "base64": base64.b64encode(part.data).decode("ascii"),
                    "mime_type": part.media_type,
                }
            )
        else:
            raise UnsupportedLangGraphContent("unsupported user message part")
    return converted


def _product_user_parts(native: HumanMessage) -> tuple[Text | Image, ...]:
    """仅读取 LangChain 1.6 的文字和内联图片；外部 URL 不在本切片范围。"""
    if isinstance(native.content, str):
        return (Text(native.content),)
    parts: list[Text | Image] = []
    for block in native.content_blocks:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(Text(block["text"]))
        elif (
            block.get("type") == "image"
            and isinstance(block.get("base64"), str)
            and block.get("mime_type") in ("image/png", "image/jpeg")
            and "url" not in block
            and "file_id" not in block
        ):
            try:
                parts.append(
                    Image(
                        block["mime_type"],
                        base64.b64decode(block["base64"], validate=True),
                    )
                )
            except (binascii.Error, ValueError):
                raise UnsupportedLangGraphContent("invalid image content") from None
        else:
            raise UnsupportedLangGraphContent("unsupported user content block")
    if not parts:
        raise UnsupportedLangGraphContent("user message is empty")
    return tuple(parts)


def _product_messages(native: Sequence[BaseMessage]) -> tuple[Message, ...]:
    """将 LangChain 工具结果并回发起它的 assistant 消息，保留调用 ID。"""
    converted: list[Message] = []
    pending: dict[str, str] = {}
    assistant_index: int | None = None
    for item in native:
        if isinstance(item, (SystemMessage, HumanMessage)):
            if pending:
                raise UnsupportedLangGraphContent("tool results are missing")
            role = "system" if isinstance(item, SystemMessage) else "user"
            parts = (
                (Text(_text(item.content)),)
                if isinstance(item, SystemMessage)
                else _product_user_parts(item)
            )
            converted.append(Message(role, parts))
        elif isinstance(item, AIMessage):
            if pending or item.invalid_tool_calls or item.additional_kwargs:
                raise UnsupportedLangGraphContent("unsupported assistant tool history")
            parts: list[Text | ToolCall | ToolResult] = []
            content = _text(item.content)
            if content:
                parts.append(Text(content))
            for call in item.tool_calls:
                call_id, name, args = call.get("id"), call.get("name"), call.get("args")
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or not isinstance(name, str)
                    or not name
                    or not isinstance(args, dict)
                    or call_id in pending
                ):
                    raise UnsupportedLangGraphContent("invalid assistant tool call")
                pending[call_id] = name
                parts.append(
                    ToolCall(call_id, name, json.dumps(args, ensure_ascii=False))
                )
            if not parts:
                raise UnsupportedLangGraphContent("assistant message is empty")
            converted.append(Message("assistant", tuple(parts)))
            assistant_index = len(converted) - 1
        elif isinstance(item, ToolMessage):
            name = pending.get(item.tool_call_id)
            if name is None or (item.name is not None and item.name != name):
                raise UnsupportedLangGraphContent("tool result does not match its call")
            if item.artifact is not None or assistant_index is None:
                raise UnsupportedLangGraphContent("tool result artifact is unsupported")
            previous = converted[assistant_index]
            converted[assistant_index] = Message(
                "assistant",
                previous.parts
                + (
                    ToolResult(
                        item.tool_call_id, name, _text(item.content), item.status
                    ),
                ),
            )
            del pending[item.tool_call_id]
        else:
            raise UnsupportedLangGraphContent(
                f"unsupported message type: {type(item).__name__}"
            )
    if pending:
        raise UnsupportedLangGraphContent("tool results are missing")
    return tuple(converted)


class _ProductChatModel(BaseChatModel):
    """只实现 LangChain Agent 本轮需要的模型接口，调用产品 ModelClient。"""

    model_id: str
    _client: ModelClient = PrivateAttr()
    _allowed_tools: dict[str, ToolSpec] = PrivateAttr()

    def __init__(
        self, model_id: str, client: ModelClient, allowed_tools: Sequence[ToolSpec]
    ) -> None:
        super().__init__(model_id=model_id)
        self._client = client
        self._allowed_tools = {spec.name: spec for spec in allowed_tools}

    @property
    def _llm_type(self) -> str:
        return "xuanyue-model-client"

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        raise NotImplementedError("this product model bridge supports async calls only")

    def bind_tools(
        self,
        tools: Sequence[BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """只绑定产品已登记的工具；额外模型选项不能被静默忽略。"""
        if kwargs or tool_choice not in (None, "auto", "none"):
            raise UnsupportedLangGraphContent("unsupported model tool option")
        specs: list[ToolSpec] = []
        for tool in tools:
            function = convert_to_openai_tool(tool)["function"]
            spec = ToolSpec(
                function["name"],
                function.get("description", ""),
                function["parameters"],
            )
            if self._allowed_tools.get(spec.name) != spec:
                raise UnsupportedLangGraphContent("framework requested an unknown tool")
            specs.append(spec)
        return self.bind(
            product_tools=tuple(specs),
            product_tool_mode="none" if tool_choice == "none" else "auto",
        )

    def _request(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None,
        kwargs: dict[str, Any],
    ) -> ModelRequest:
        """非流式调用和 token 流都使用同一组工具及选项检查。"""
        if stop or set(kwargs) - {"product_tools", "product_tool_mode"}:
            raise UnsupportedLangGraphContent("unsupported model generation option")
        specs = kwargs.get("product_tools", ())
        tool_mode = kwargs.get("product_tool_mode", "auto")
        if not isinstance(specs, tuple) or tool_mode not in ("auto", "none"):
            raise UnsupportedLangGraphContent("invalid bound tool option")
        return ModelRequest(
            self.model_id, _product_messages(messages), specs, tool_mode
        )

    @staticmethod
    def _reply_message(reply: ModelReply, request: ModelRequest) -> AIMessage:
        """在让图执行工具前验证最终回复及工具范围。"""
        if not isinstance(reply, ModelReply):
            raise TypeError("ModelClient must return ModelReply")
        text_parts: list[str] = []
        calls: list[dict[str, object]] = []
        for part in reply.parts:
            if isinstance(part, Text):
                text_parts.append(part.value)
            elif isinstance(part, ToolCall):
                if request.tool_mode == "none" or part.name not in {
                    s.name for s in request.tools
                }:
                    raise UnsupportedLangGraphContent(
                        "model called an unavailable tool"
                    )
                try:
                    args = json.loads(part.arguments)
                except json.JSONDecodeError as exc:
                    raise UnsupportedLangGraphContent(
                        "invalid tool arguments JSON"
                    ) from exc
                if not isinstance(args, dict) or not part.id:
                    raise UnsupportedLangGraphContent(
                        "tool arguments must be an object"
                    )
                calls.append({"id": part.id, "name": part.name, "args": args})
            else:
                raise UnsupportedLangGraphContent("unsupported model reply part")
        if not text_parts and not calls:
            raise UnsupportedLangGraphContent("model returned an empty reply")
        return AIMessage(content="".join(text_parts), tool_calls=calls)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        request = self._request(messages, stop, kwargs)
        message = self._reply_message(await self._client.complete(request), request)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """将产品模型增量交给 LangGraph 消息流，最终工具调用仍经图执行。"""
        request = self._request(messages, stop, kwargs)
        visible: list[str] = []
        finished = False
        async for chunk in self._client.stream(request):
            if finished:
                raise UnsupportedLangGraphContent("model streamed after final reply")
            if chunk.text_delta is not None:
                if not isinstance(chunk.text_delta, str):
                    raise UnsupportedLangGraphContent("invalid text delta")
                if chunk.text_delta:
                    visible.append(chunk.text_delta)
                    yield ChatGenerationChunk(
                        message=AIMessageChunk(content=chunk.text_delta)
                    )
            if chunk.reply is None:
                continue
            reply = self._reply_message(chunk.reply, request)
            complete_text = _text(reply.content)
            streamed_text = "".join(visible)
            if streamed_text and streamed_text != complete_text:
                raise UnsupportedLangGraphContent(
                    "stream text differs from final reply"
                )
            if complete_text and not streamed_text:
                yield ChatGenerationChunk(message=AIMessageChunk(content=complete_text))
            if reply.tool_calls:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "id": call["id"],
                                "name": call["name"],
                                "args": json.dumps(call["args"], ensure_ascii=False),
                                "index": index,
                            }
                            for index, call in enumerate(reply.tool_calls)
                        ],
                    )
                )
            finished = True
        if not finished:
            raise UnsupportedLangGraphContent("model stream ended without final reply")


def _native_tool(spec: ToolSpec, task: Task, tools: ToolService) -> StructuredTool:
    """LangGraph 执行工具时仍回到产品 ToolService 逐次校验和授权。"""

    async def invoke(**arguments: object) -> str:
        result = await tools.invoke(task, spec.name, arguments)
        if not isinstance(result, str):
            raise TypeError(f"tool {spec.name!r} must return text")
        return result

    return StructuredTool.from_function(
        coroutine=invoke,
        name=spec.name,
        description=spec.description,
        args_schema=dict(spec.input_schema),
    )


class LangGraphKernel(AgentKernel):
    """让 LangGraph 编译的 Agent 独立运行产品文字任务。"""

    id = "langgraph"

    def __init__(
        self, model: ModelClient, tools: ToolService, system_prompt: str
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt must be non-empty")
        self._model = model
        self._tools = tools
        self._system_prompt = system_prompt

    async def stream(self, task: Task) -> AsyncIterator[Event]:
        """投影已验证的 model/tools 更新；未知节点不冒充成功轨迹。"""
        if task.kernel != self.id:
            raise ValueError("task kernel does not match LangGraph")
        specs = self._tools.specs()
        names = [spec.name for spec in specs]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("tool names must be non-empty and unique")
        graph = create_agent(
            model=_ProductChatModel(task.model, self._model, specs),
            tools=[_native_tool(spec, task, self._tools) for spec in specs],
            system_prompt=self._system_prompt,
        )
        seq = 0
        model_count = 1
        tool_count = 0
        current_model: str | None = "model-1"
        tools_by_call_id: dict[str, tuple[str, str]] = {}
        pending_tool_ids: set[str] = set()

        def event(kind: str, payload: Mapping[str, object]) -> Event:
            nonlocal seq
            seq += 1
            return Event(task.run_id, seq, kind, payload)

        yield event("reply_started", {"name": "primary-agent"})
        yield event(
            "model_call_started",
            {"model_name": task.model, "activity_id": current_model},
        )
        completed = False
        # 图每轮重新构建；产品层提供之前已完成的公开对话，保持两种内核
        # 收到相同的公开上下文，而不依赖某个框架的私有内存格式。
        inputs: list[BaseMessage] = []
        for message in task.history:
            if message.role == "user":
                inputs.append(HumanMessage(content=_native_user_content(message.parts)))
            else:
                content = "".join(part.value for part in message.parts)
                inputs.append(AIMessage(content=content))
        inputs.append(HumanMessage(content=_native_user_content(task.user_parts)))
        async for mode, update in graph.astream(
            {"messages": inputs},
            stream_mode=["messages", "updates"],
            config={"recursion_limit": 8},
        ):
            if mode == "messages":
                chunk, metadata = update
                if metadata.get("langgraph_node") != "model":
                    continue
                if not isinstance(chunk, AIMessageChunk):
                    raise UnsupportedLangGraphContent("unexpected graph stream chunk")
                if not isinstance(chunk.content, str):
                    raise UnsupportedLangGraphContent("non-text graph stream chunk")
                if chunk.content:
                    if current_model is None:
                        raise UnsupportedLangGraphContent(
                            "text has no active model call"
                        )
                    yield event(
                        "text_delta",
                        {"delta": chunk.content, "activity_id": current_model},
                    )
                continue
            if mode != "updates":
                raise UnsupportedLangGraphContent("unexpected graph stream mode")
            if not isinstance(update, dict) or len(update) != 1:
                raise UnsupportedLangGraphContent("unexpected graph update")
            node, value = next(iter(update.items()))
            if not isinstance(value, dict) or not isinstance(
                value.get("messages"), list
            ):
                raise UnsupportedLangGraphContent("unexpected graph messages")
            messages = value["messages"]
            if (
                node == "model"
                and len(messages) == 1
                and isinstance(messages[0], AIMessage)
            ):
                response = messages[0]
                if response.invalid_tool_calls or not isinstance(response.content, str):
                    raise UnsupportedLangGraphContent("unsupported graph model reply")
                if current_model is None:
                    raise UnsupportedLangGraphContent("model update has no active call")
                # 文字已从 messages 流逐块发出；updates 仅确定本轮工具与终态。
                for call in response.tool_calls:
                    call_id, name, args = (
                        call.get("id"),
                        call.get("name"),
                        call.get("args"),
                    )
                    if (
                        not isinstance(call_id, str)
                        or not call_id
                        or not isinstance(name, str)
                        or not isinstance(args, dict)
                    ):
                        raise UnsupportedLangGraphContent("invalid graph tool call")
                    if call_id in tools_by_call_id:
                        raise UnsupportedLangGraphContent(
                            "duplicate graph tool call ID cannot be linked"
                        )
                    # graph 的 model 更新明确包含这些工具调用，父级来自本次调用 ID。
                    tool_count += 1
                    activity_id = f"tool-{tool_count}"
                    tools_by_call_id[call_id] = (activity_id, current_model)
                    pending_tool_ids.add(call_id)
                    yield event(
                        "tool_call_started",
                        {
                            "tool_call_id": call_id,
                            "tool_call_name": name,
                            "activity_id": activity_id,
                            "parent_activity_id": current_model,
                        },
                    )
                    # 图更新是完整消息，不是 token 流；仍保留用户可见的工具入参。
                    yield event(
                        "tool_call_delta",
                        {
                            "tool_call_id": call_id,
                            "delta": json.dumps(args, ensure_ascii=False),
                            "activity_id": activity_id,
                            "parent_activity_id": current_model,
                        },
                    )
                    yield event(
                        "tool_call_finished",
                        {
                            "tool_call_id": call_id,
                            "activity_id": activity_id,
                            "parent_activity_id": current_model,
                        },
                    )
                yield event(
                    "model_call_finished",
                    {"usage_status": "unknown", "activity_id": current_model},
                )
                current_model = None
                completed = not response.tool_calls
            elif (
                node == "tools"
                and messages
                and all(isinstance(msg, ToolMessage) for msg in messages)
            ):
                for result in messages:
                    linked = tools_by_call_id.get(result.tool_call_id)
                    if linked is None or result.tool_call_id not in pending_tool_ids:
                        raise UnsupportedLangGraphContent(
                            "tool result has no pending parent call"
                        )
                    activity_id, parent_activity_id = linked
                    yield event(
                        "tool_result_started",
                        {
                            "tool_call_id": result.tool_call_id,
                            "tool_call_name": result.name,
                            "activity_id": activity_id,
                            "parent_activity_id": parent_activity_id,
                        },
                    )
                    yield event(
                        "tool_result_delta",
                        {
                            "tool_call_id": result.tool_call_id,
                            "delta": _text(result.content),
                            "activity_id": activity_id,
                            "parent_activity_id": parent_activity_id,
                        },
                    )
                    yield event(
                        "tool_result_finished",
                        {
                            "tool_call_id": result.tool_call_id,
                            "state": result.status,
                            "activity_id": activity_id,
                            "parent_activity_id": parent_activity_id,
                        },
                    )
                    pending_tool_ids.remove(result.tool_call_id)
                # LangGraph 可逐个报告并行工具的结果；全部收齐后才有下一次模型调用。
                if not pending_tool_ids:
                    model_count += 1
                    current_model = f"model-{model_count}"
                    yield event(
                        "model_call_started",
                        {"model_name": task.model, "activity_id": current_model},
                    )
            else:
                raise UnsupportedLangGraphContent(f"unexpected graph node: {node}")
        if not completed:
            raise UnsupportedLangGraphContent("graph ended without a final answer")
        if pending_tool_ids:
            raise UnsupportedLangGraphContent("graph ended with missing tool results")
        yield event("reply_finished", {"finished_reason": "completed"})
