"""将产品模型 ID 精确绑定到某个协议客户端和上游模型名。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from xuanyue.interfaces import ModelClient
from xuanyue.types import ModelReply, ModelRequest


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """单个模型的运行时登记项；调用方负责客户端密钥与生命周期。"""

    upstream_model: str
    client: ModelClient

    def __post_init__(self) -> None:
        if not self.upstream_model.strip():
            raise ValueError("upstream model must be non-empty")


class ModelUnavailable(LookupError):
    """请求了尚未登记的产品模型 ID。"""

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f"model {model!r} is not registered")


class ModelRouter(ModelClient):
    """内核只传产品模型 ID；本层选择客户端并替换为上游模型名。"""

    def __init__(self, routes: Mapping[str, ModelRoute]) -> None:
        if any(not name.strip() for name in routes):
            raise ValueError("product model ids must be non-empty")
        self._routes = dict(routes)

    async def complete(self, request: ModelRequest) -> ModelReply:
        try:
            route = self._routes[request.model]
        except KeyError as exc:
            raise ModelUnavailable(request.model) from exc
        return await route.client.complete(replace(request, model=route.upstream_model))
