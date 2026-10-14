from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pytest

from castle_agents.adapters.mock import MockRouter
from castle_agents.domain import Inbound, Outbound, Steering
from castle_agents.observability import EventLog
from castle_agents.runtime import (
    Agents,
    Dispatcher,
    Escalate,
    History,
    OnEvent,
    Researcher,
    Router,
)
from castle_agents.settings import Settings, load_settings

WAIT_S = 5.0


class Scripted:
    """Quick and research answers are distinguishable, and each waits until the test
    releases it (or for a fixed delay), so a test chooses exactly when work finishes."""

    def __init__(self, quick_delay_s: float | None = None) -> None:
        self.gates: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self.quick_delay_s = quick_delay_s
        self.quick_calls: list[str] = []
        self.research_calls: list[str] = []
        self.cancelled: list[tuple[str, str]] = []

    def release(self, *texts: str) -> None:
        for text in texts:
            self.gates[text].set()

    async def answer(self, text: str, history: History) -> str:
        self.quick_calls.append(text)
        try:
            if self.quick_delay_s is None:
                await self.gates[text].wait()
            else:
                await asyncio.sleep(self.quick_delay_s)
        except asyncio.CancelledError:
            self.cancelled.append(("quick", text))
            raise
        if "latest" in text:
            raise Escalate()
        return f"quick:{text}"

    async def research(
        self, text: str, history: History, steering: Steering, on_event: OnEvent
    ) -> str:
        self.research_calls.append(text)
        try:
            await self.gates[text].wait()
        except asyncio.CancelledError:
            self.cancelled.append(("research", text))
            raise
        applied = steering.drain()
        for addition in applied:
            on_event("steer_applied", addition=addition.id, text=addition.text, at="tool_boundary")
        return f"research:{text}" + "".join(f" +{a.text}" for a in applied)


class DelayedRouter:
    def __init__(self, inner: Router, delay_s: float) -> None:
        self.inner, self.delay_s = inner, delay_s

    async def route(self, batch: Any, recent: Any) -> Any:
        await asyncio.sleep(self.delay_s)
        return await self.inner.route(batch, recent)


class Harness:
    def __init__(
        self,
        settings: Settings,
        router: Router | None = None,
        researcher: Researcher | None = None,
        quick_delay_s: float | None = None,
        **limit_overrides: Any,
    ) -> None:
        self.log = EventLog()
        self.scripted = Scripted(quick_delay_s)
        self.posts: list[Outbound] = []
        self.started = asyncio.get_running_loop().time()
        self.posted_at: list[float] = []
        agents = Agents(
            router or instant_router(settings), self.scripted, researcher or self.scripted
        )
        limits = settings.limits.override(**limit_overrides)
        self.d = Dispatcher(agents, self.log, limits, settings.phrases, self._record)
        self._said: defaultdict[str, int] = defaultdict(int)

    def _record(self, out: Outbound) -> None:
        self.posts.append(out)
        self.posted_at.append(asyncio.get_running_loop().time() - self.started)

    def release(self, *texts: str) -> None:
        self.scripted.release(*texts)

    def say(
        self, text: str, user: str = "alice", reply_to: str | None = None, chat: str = "c"
    ) -> str:
        self._said[chat] += 1
        msg_id = f"m{self._said[chat]}"
        self.d.submit(Inbound(chat, msg_id, user, text, reply_to))
        return msg_id

    async def routed(self, chat: str = "c") -> None:
        expected = self._said[chat]
        await self.until(lambda: self._routes(chat) >= expected)

    async def idle(self) -> None:
        async with asyncio.timeout(WAIT_S):
            await self.d.wait_idle()

    async def until(self, condition: Callable[[], bool]) -> None:
        async with asyncio.timeout(WAIT_S):
            while not condition():  # noqa: ASYNC110 - waits on an arbitrary predicate
                await asyncio.sleep(0.001)
        await asyncio.sleep(0)

    def slot_posts(self) -> list[Outbound]:
        return [p for p in self.posts if p.fills_slot]

    def _routes(self, chat: str) -> int:
        return sum(1 for e in self.log.events if e["type"] == "route" and e["chat"] == chat)


def instant_router(settings: Settings) -> MockRouter:
    return MockRouter(settings.mock.scaled(0))


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture
def make_harness(settings: Settings) -> Callable[..., Harness]:
    def make(**kwargs: Any) -> Harness:
        return Harness(settings, **kwargs)

    return make
