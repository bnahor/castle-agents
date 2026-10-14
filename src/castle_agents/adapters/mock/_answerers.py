from __future__ import annotations

import asyncio
import zlib

from ...domain import Addition, Steering
from ...runtime import Escalate, History, MarketTools, OnEvent
from ._config import MockConfig, anywhere
from ._router import TICKER
from ._tools import MockTools


class MockQuickAnswerer:
    def __init__(self, config: MockConfig) -> None:
        self._latency = config.quick_latency_s
        self._needs_data = anywhere(config.patterns.needs_data)
        self._reply = config.replies.quick

    async def answer(self, text: str, history: History) -> str:
        await asyncio.sleep(self._latency)
        if self._needs_data.search(text):
            raise Escalate()
        return self._reply.format(text=text)


class MockResearcher:
    def __init__(self, config: MockConfig, tools: MarketTools | None = None) -> None:
        self._config = config
        self._tools = tools or MockTools()

    async def research(
        self, text: str, history: History, steering: Steering, on_event: OnEvent
    ) -> str:
        step = self._duration(text) / self._config.research_steps
        tickers = TICKER.findall(text) or ["SPY"]
        applied: list[Addition] = []
        for i in range(self._config.research_steps):
            await asyncio.sleep(step)
            await self._look_up(tickers[i % len(tickers)], on_event)
            applied += _apply(steering, on_event, "tool_boundary")
        while steering.pending():
            await asyncio.sleep(step / 2)
            applied += _apply(steering, on_event, "final_check")
        return self._reply(text, applied)

    def _duration(self, text: str) -> float:
        jitter = (zlib.crc32(text.encode()) % 1000) / 1000
        return self._config.research_base_s + self._config.research_spread_s * jitter

    async def _look_up(self, ticker: str, on_event: OnEvent) -> None:
        on_event("tool", name="get_previous_close", ticker=ticker)
        await self._tools.call("get_previous_close", {"ticker": ticker})

    def _reply(self, text: str, applied: list[Addition]) -> str:
        replies = self._config.replies
        covered = "; ".join(addition.text for addition in applied)
        extra = replies.also_covering.format(additions=covered) if applied else ""
        return replies.research.format(text=text) + extra


def _apply(steering: Steering, on_event: OnEvent, at: str) -> list[Addition]:
    additions = steering.drain()
    for addition in additions:
        on_event("steer_applied", addition=addition.id, text=addition.text, at=at)
    return additions
