"""Choose the exact root kernel requested by the user."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from .contracts import Event, Kernel, Task


class KernelUnavailable(LookupError):
    def __init__(self, kernel: str) -> None:
        self.kernel = kernel
        super().__init__(f"kernel {kernel!r} is not registered")


class Runtime:
    def __init__(self, kernels: Iterable[Kernel]) -> None:
        self._kernels: dict[str, Kernel] = {}
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
