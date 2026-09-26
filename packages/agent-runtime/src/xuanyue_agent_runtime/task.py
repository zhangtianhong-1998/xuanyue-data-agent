"""Internal text-task boundary; broader Agent and workflow contracts remain under review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class TextTaskRequest:
    run_id: str
    kernel_id: str
    text: str

    def __post_init__(self) -> None:
        for name in ("run_id", "kernel_id", "text"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class PublicEvent:
    run_id: str
    seq: int
    kind: str
    payload: Mapping[str, object]


class KernelNotRegistered(LookupError):
    def __init__(self, kernel_id: str) -> None:
        self.kernel_id = kernel_id
        super().__init__(f"kernel {kernel_id!r} is not registered")


class TextTaskRunner(Protocol):
    """Only the current text-task operation; not a full runtime SDK."""

    kernel_id: str

    def stream(self, request: TextTaskRequest) -> AsyncIterator[PublicEvent]: ...


class RuntimeRegistry:
    """Resolve exactly the kernel selected by the caller."""

    def __init__(self, runners: Iterable[TextTaskRunner]) -> None:
        self._runners: dict[str, TextTaskRunner] = {}
        for runner in runners:
            if not runner.kernel_id or runner.kernel_id in self._runners:
                raise ValueError(f"invalid or duplicate kernel id: {runner.kernel_id!r}")
            self._runners[runner.kernel_id] = runner

    def stream(self, request: TextTaskRequest) -> AsyncIterator[PublicEvent]:
        try:
            runner = self._runners[request.kernel_id]
        except KeyError as exc:
            raise KernelNotRegistered(request.kernel_id) from exc
        return runner.stream(request)
