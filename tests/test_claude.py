from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from anthropic import AsyncAnthropic

from castle_agents.adapters.claude import ClaudeConfig, ClaudeQuickAnswerer, ClaudeResearcher
from castle_agents.adapters.mock import MockTools
from castle_agents.domain import Addition, Steering
from castle_agents.runtime import Escalate
from castle_agents.settings import Settings


def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def _tool(id: str, ticker: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use", id=id, name="get_previous_close", input={"ticker": ticker}
    )


def _resp(stop: str, *content: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop,
        content=list(content),
        model="fake",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class FakeMessages:
    def __init__(self, responses: list[SimpleNamespace], during_call: Any = None) -> None:
        self.responses = responses
        self.calls: list[list[dict[str, Any]]] = []
        self.during_call = during_call

    async def create(self, **kw: Any) -> SimpleNamespace:
        self.calls.append([dict(m) for m in kw["messages"]])
        if self.during_call:
            self.during_call(len(self.calls))
        await asyncio.sleep(0)
        return self.responses.pop(0)


def _client(messages: FakeMessages) -> AsyncAnthropic:
    fake = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
    return cast(AsyncAnthropic, fake)


def _ignore(kind: str, **data: Any) -> None:
    pass


async def test_tool_results_and_steering_go_back_in_one_user_turn(settings: Settings):
    steering = Steering()
    fake = FakeMessages(
        [
            _resp("tool_use", _text("checking"), _tool("t1", "NVDA"), _tool("t2", "AMD")),
            _resp("end_turn", _text("NVDA beat AMD.")),
        ],
        during_call=lambda n: steering.push(Addition("m3", "include INTC"), 1) if n == 1 else None,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    researcher = ClaudeResearcher(_client(fake), MockTools(), settings.claude)
    answer = await researcher.research(
        "compare NVDA and AMD", [], steering, lambda kind, **d: events.append((kind, d))
    )
    assert answer == "NVDA beat AMD."
    user_turn = fake.calls[1][-1]["content"]
    assert [b["type"] for b in user_turn] == ["tool_result", "tool_result", "text"]
    assert user_turn[2]["text"] == f"{settings.claude.steer_prefix} include INTC"
    applied = {"addition": "m3", "text": "include INTC", "at": "tool_boundary"}
    assert ("steer_applied", applied) in events


async def test_addition_during_the_final_call_triggers_one_more_turn(settings: Settings):
    steering = Steering()
    fake = FakeMessages(
        [_resp("end_turn", _text("draft")), _resp("end_turn", _text("final with INTC"))],
        during_call=lambda n: steering.push(Addition("m3", "include INTC"), 1) if n == 1 else None,
    )
    researcher = ClaudeResearcher(_client(fake), MockTools(), settings.claude)
    assert await researcher.research("q", [], steering, _ignore) == "final with INTC"
    second = fake.calls[1]
    assert second[1]["role"] == "assistant"
    assert second[2]["content"][0]["text"] == f"{settings.claude.steer_prefix} include INTC"


async def test_tool_errors_go_back_to_the_model_as_error_results(settings: Settings):
    class Broken(MockTools):
        async def call(self, name: str, args: dict[str, Any]) -> str:
            raise ConnectionError("massive unreachable")

    fake = FakeMessages(
        [_resp("tool_use", _tool("t1", "NVDA")), _resp("end_turn", _text("data unavailable"))]
    )
    researcher = ClaudeResearcher(_client(fake), Broken(), settings.claude)
    assert await researcher.research("q", [], Steering(), _ignore) == "data unavailable"
    result = fake.calls[1][-1]["content"][0]
    assert result["is_error"] is True and "unreachable" in result["content"]


async def test_a_stalled_tool_call_times_out_into_an_error_result(settings: Settings):
    class Stalls(MockTools):
        async def call(self, name: str, args: dict[str, Any]) -> str:
            await asyncio.sleep(10)
            return "never"

    fake = FakeMessages(
        [_resp("tool_use", _tool("t1", "NVDA")), _resp("end_turn", _text("data unavailable"))]
    )
    config: ClaudeConfig = settings.claude.model_copy(update={"tool_timeout_s": 0.05})
    researcher = ClaudeResearcher(_client(fake), Stalls(), config)
    assert await researcher.research("q", [], Steering(), _ignore) == "data unavailable"
    result = fake.calls[1][-1]["content"][0]
    assert result["is_error"] is True and "TimeoutError" in result["content"]


async def test_quick_path_escalates_on_the_sentinel(settings: Settings):
    fake = FakeMessages([_resp("end_turn", _text(settings.claude.escalate_sentinel))])
    with pytest.raises(Escalate):
        await ClaudeQuickAnswerer(_client(fake), settings.claude).answer("latest price?", [])
