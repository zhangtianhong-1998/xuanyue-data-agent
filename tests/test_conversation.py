"""两套真实内核应接收相同的已完成公开对话和当前图片。"""

from __future__ import annotations

import unittest

from agentscope.message import Base64Source, DataBlock, URLSource, UserMsg
from langchain_core.messages import HumanMessage

from xuanyue import Runtime, Task
from xuanyue.engines.agentscope import (
    AgentScopeKernel,
    UnsupportedModelContent,
    _model_message,
)
from xuanyue.engines.langgraph import (
    LangGraphKernel,
    UnsupportedLangGraphContent,
    _product_messages,
)
from xuanyue.llm import ModelRoute, ModelRouter
from xuanyue.tools import LocalTools
from xuanyue.types import Image, Message, ModelReply, ModelRequest, Text, ToolCall

_PNG = b"\x89PNG\r\n\x1a\nsynthetic-image"


class _RecordingModel:
    """只记录内核实际提交的请求，不模拟内核的对话处理。"""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        return ModelReply((Text("第二轮答复"),))


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_engines_receive_completed_text_history_in_order(self) -> None:
        history = (
            Message("user", (Text("第一轮问题"),)),
            Message("assistant", (Text("第一轮答复"),)),
        )
        for kernel_class in (AgentScopeKernel, LangGraphKernel):
            with self.subTest(kernel=kernel_class.id):
                model = _RecordingModel()
                runtime = Runtime(
                    [
                        kernel_class(
                            ModelRouter({"chosen": ModelRoute("upstream", model)}),
                            LocalTools([]),
                            "请简短回答。",
                        )
                    ]
                )
                task = Task(
                    "second-turn",
                    kernel_class.id,
                    "chosen",
                    "第二轮问题",
                    history=history,
                )

                events = [event async for event in runtime.stream(task)]

                self.assertEqual(events[-1].kind, "reply_finished")
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(model.requests[0].model, "upstream")
                self.assertEqual(
                    [
                        (message.role, tuple(part.value for part in message.parts))
                        for message in model.requests[0].messages
                    ],
                    [
                        ("system", ("请简短回答。",)),
                        ("user", ("第一轮问题",)),
                        ("assistant", ("第一轮答复",)),
                        ("user", ("第二轮问题",)),
                    ],
                )

    async def test_both_engines_preserve_images_in_history_and_current_turn(
        self,
    ) -> None:
        earlier = Image("image/png", _PNG)
        current = Image("image/jpeg", b"\xff\xd8\xffsynthetic-image")
        history = (
            Message("user", (Text("先看这张图"), earlier, Text("只需简述"))),
            Message("assistant", (Text("第一轮答复"),)),
        )
        for kernel_class in (AgentScopeKernel, LangGraphKernel):
            with self.subTest(kernel=kernel_class.id):
                model = _RecordingModel()
                runtime = Runtime(
                    [
                        kernel_class(
                            ModelRouter({"chosen": ModelRoute("upstream", model)}),
                            LocalTools([]),
                            "请简短回答。",
                        )
                    ]
                )
                events = [
                    event
                    async for event in runtime.stream(
                        Task(
                            "image-turn",
                            kernel_class.id,
                            "chosen",
                            "再看另一张图",
                            history=history,
                            images=(current,),
                        )
                    )
                ]
                self.assertEqual(events[-1].kind, "reply_finished")
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(model.requests[0].messages[1], history[0])
                self.assertEqual(
                    model.requests[0].messages[-1],
                    Message("user", (Text("再看另一张图"), current)),
                )
                self.assertFalse(
                    any(
                        str(event.payload).find("synthetic-image") >= 0
                        for event in events
                    )
                )

    def test_native_images_with_unsupported_sources_fail_closed(self) -> None:
        # AgentScope 2.0.8 的 DataBlock 也可表示 URL 和其他模态；本切片不接纳它们。
        with self.assertRaisesRegex(UnsupportedModelContent, "unsupported image"):
            _model_message(
                UserMsg(
                    "user",
                    [
                        DataBlock(
                            source=Base64Source(data="AA==", media_type="audio/wav")
                        )
                    ],
                )
            )
        with self.assertRaisesRegex(UnsupportedModelContent, "unsupported image"):
            _model_message(
                UserMsg(
                    "user",
                    [
                        DataBlock(
                            source=URLSource(
                                url="https://example.com/a.png", media_type="image/png"
                            )
                        )
                    ],
                )
            )
        with self.assertRaisesRegex(UnsupportedModelContent, "invalid image"):
            _model_message(
                UserMsg(
                    "user",
                    [
                        DataBlock(
                            source=Base64Source(
                                data="not base64", media_type="image/png"
                            )
                        )
                    ],
                )
            )
        with self.assertRaisesRegex(UnsupportedLangGraphContent, "unsupported user"):
            _product_messages(
                [
                    HumanMessage(
                        content=[{"type": "image", "url": "https://example.com/a.png"}]
                    )
                ]
            )

    def test_task_rejects_incomplete_or_non_text_history(self) -> None:
        invalid_histories = {
            "unfinished_user_turn": (Message("user", (Text("第一轮问题"),)),),
            "wrong_order": (
                Message("assistant", (Text("第一轮答复"),)),
                Message("user", (Text("第一轮问题"),)),
            ),
            "tool_call": (
                Message("user", (Text("第一轮问题"),)),
                Message("assistant", (ToolCall("call-1", "multiply", "{}"),)),
            ),
            "private_content": (
                Message("user", (Text("第一轮问题"),)),
                Message("assistant", (Text("第一轮答复"),), True),
            ),
            "system_message": (
                Message("system", (Text("旧系统提示"),)),
                Message("assistant", (Text("第一轮答复"),)),
            ),
        }
        for name, history in invalid_histories.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                Task(
                    "second-turn", "agentscope", "chosen", "第二轮问题", history=history
                )

    def test_task_rejects_more_than_four_images_per_turn(self) -> None:
        images = tuple(Image("image/png", _PNG + bytes([i])) for i in range(5))
        with self.assertRaisesRegex(ValueError, "at most four images"):
            Task("too-many", "agentscope", "chosen", "请看图", images=images)
        with self.assertRaisesRegex(ValueError, "at most four images"):
            Task(
                "too-many-in-history",
                "langgraph",
                "chosen",
                "请继续",
                history=(
                    Message("user", (Text("上一轮"), *images)),
                    Message("assistant", (Text("已处理"),)),
                ),
            )
