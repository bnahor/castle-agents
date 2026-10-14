from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from ._enums import Kind, Status


@dataclass(frozen=True, slots=True)
class Addition:
    id: str
    text: str


class Steering:
    """One generation's inbox. Delivery names the generation it expects, so an
    addition can never reach a worker from a superseded generation."""

    def __init__(self, generation: int = 1) -> None:
        self.generation = generation
        self._pending: deque[Addition] = deque()
        self._closed = False

    def push(self, addition: Addition, generation: int) -> bool:
        if self._closed or generation != self.generation:
            return False
        self._pending.append(addition)
        return True

    def close(self) -> None:
        self._closed = True
        self._pending.clear()

    def drain(self) -> list[Addition]:
        drained = list(self._pending)
        self._pending.clear()
        return drained

    def pending(self) -> bool:
        return bool(self._pending)


@dataclass(slots=True)
class Request:
    id: str
    chat_id: str
    seq: int
    kind: Kind
    text: str
    user: str
    msg_ids: list[str]
    additions: list[Addition] = field(default_factory=list[Addition])
    version: int = 1
    status: Status = Status.QUEUED
    steering: Steering = field(default_factory=Steering)
    task: asyncio.Task[None] | None = None
    head_start: asyncio.Task[str] | None = None

    def is_current(self, version: int) -> bool:
        return self.version == version and self.status is not Status.CANCELLED

    def cancel_task(self) -> None:
        for task in (self.task, self.head_start):
            if task and not task.done():
                task.cancel()
        self.head_start = None


@dataclass(frozen=True, slots=True)
class RequestView:
    id: str
    seq: int
    user: str
    text: str
    status: Status
    msg_ids: tuple[str, ...]

    @classmethod
    def of(cls, request: Request, text: str) -> RequestView:
        return cls(
            request.id, request.seq, request.user, text, request.status, tuple(request.msg_ids)
        )
