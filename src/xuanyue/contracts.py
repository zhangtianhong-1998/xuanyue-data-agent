"""Objects shared by the product and every Agent kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class Task:
    run_id: str
    kernel: str
    model: str
    text: str

    def __post_init__(self) -> None:
        for field in ("run_id", "kernel", "model", "text"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Event:
    run_id: str
    seq: int
    kind: str
    payload: Mapping[str, object]


class Kernel(Protocol):
    id: str

    def stream(self, task: Task) -> AsyncIterator[Event]: ...
