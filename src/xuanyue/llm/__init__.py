"""模型接入的公开入口；协议转换与产品模型路由各有独立模块。"""

from .openai_compatible import ChatCompletionsClient, UnsupportedChatContent
from .router import ModelRoute, ModelRouter, ModelUnavailable

__all__ = [
    "ChatCompletionsClient",
    "ModelRoute",
    "ModelRouter",
    "ModelUnavailable",
    "UnsupportedChatContent",
]
