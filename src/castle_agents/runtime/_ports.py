from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain import Exchange, Inbound, RequestView, Route, Steering

History = Sequence[Exchange]
OnEvent = Callable[..., None]
UsageLog = Callable[[str, Any], None]


class Escalate(Exception):
    pass


class Unsuccessful(Exception):
    """A worker that ends without a real answer, with the text to post in its slot."""

    def __init__(self, text: str, reason: str) -> None:
        super().__init__(reason)
        self.text = text
        self.reason = reason


class Router(Protocol):
    async def route(self, batch: Sequence[Inbound], recent: list[RequestView]) -> list[Route]: ...


class QuickAnswerer(Protocol):
    async def answer(self, text: str, history: History) -> str: ...


class Researcher(Protocol):
    async def research(
        self, text: str, history: History, steering: Steering, on_event: OnEvent
    ) -> str: ...


class MarketTools(Protocol):
    async def call(self, name: str, args: dict[str, Any]) -> str: ...


class EventSink(Protocol):
    def now(self) -> float: ...

    def append(self, chat: str, type: str, **data: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class Agents:
    router: Router
    quick: QuickAnswerer
    researcher: Researcher
