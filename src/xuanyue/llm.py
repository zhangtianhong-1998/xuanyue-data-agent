"""Route each model call to the exact model registered by the product."""

from __future__ import annotations

from collections.abc import Mapping

from .ports import ModelPort
from .types import ModelReply, ModelRequest


class ModelUnavailable(LookupError):
    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f"model {model!r} is not registered")


class ModelRouter(ModelPort):
    """按模型 ID 分派给已登记后端；这里尚不实现供应商 API。"""

    def __init__(self, backends: Mapping[str, ModelPort]) -> None:
        if any(not name for name in backends):
            raise ValueError("model ids must be non-empty")
        self._backends = dict(backends)

    async def complete(self, request: ModelRequest) -> ModelReply:
        try:
            backend = self._backends[request.model]
        except KeyError as exc:
            raise ModelUnavailable(request.model) from exc
        return await backend.complete(request)
