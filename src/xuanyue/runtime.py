"""Choose the exact root kernel requested by the user."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping

from xuanyue.interfaces import AgentKernel
from xuanyue.types import Event, Task


class KernelUnavailable(LookupError):
    def __init__(self, kernel: str) -> None:
        self.kernel = kernel
        super().__init__(f"kernel {kernel!r} is not registered")


class InvalidKernelEvent(ValueError):
    """引擎输出的公开事件不符合当前任务的最小信封契约。"""


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
        return self._validated_stream(kernel, task)

    async def _validated_stream(
        self, kernel: AgentKernel, task: Task
    ) -> AsyncIterator[Event]:
        """只校验可通用检查的 run、顺序和载荷形状；内容由适配器负责。"""
        expected_seq = 1
        async for event in kernel.stream(task):
            if (
                not isinstance(event, Event)
                or event.run_id != task.run_id
                or type(event.seq) is not int
                or event.seq != expected_seq
                or not isinstance(event.kind, str)
                or not event.kind
                or not isinstance(event.payload, Mapping)
            ):
                raise InvalidKernelEvent("engine emitted an invalid public event")
            yield event
            expected_seq += 1
