"""Choose the exact root kernel requested by the user."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from .ports import AgentKernel
from .types import Event, Task


class KernelUnavailable(LookupError):
    def __init__(self, kernel: str) -> None:
        self.kernel = kernel
        super().__init__(f"kernel {kernel!r} is not registered")


class Runtime:
    """按任务指定的内核分派；未登记时拒绝，不自动替换内核。"""

    def __init__(self, kernels: Iterable[AgentKernel]) -> None:
        self._kernels: dict[str, AgentKernel] = {}
        for kernel in kernels:
            if not kernel.id or kernel.id in self._kernels:
                raise ValueError(f"invalid or duplicate kernel id: {kernel.id!r}")
            self._kernels[kernel.id] = kernel

    def stream(self, task: Task) -> AsyncIterator[Event]:
        try:
            kernel = self._kernels[task.kernel]
        except KeyError as exc:
            raise KernelUnavailable(task.kernel) from exc
        return kernel.stream(task)
