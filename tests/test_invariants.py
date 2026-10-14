from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from castle_agents.adapters.mock import mock_agents
from castle_agents.observability import EventLog, Metrics, compute
from castle_agents.runtime import Limits, Ordering
from castle_agents.settings import load_settings
from castle_agents.simulation import Step, load_random_pools, load_scenarios, play, random_scenario

SETTINGS = load_settings()
POOLS = load_random_pools(SETTINGS.scenarios_dir)
SCALE = 0.004

seeds = st.integers(0, 1_000_000)
sizes = st.integers(1, 18)


def _limits(**overrides: Any) -> Limits:
    return SETTINGS.limits.override(**overrides)


def _run(steps: Iterable[Step], limits: Limits, scale: float = SCALE) -> Metrics:
    log = EventLog()
    agents = mock_agents(SETTINGS.mock, scale)
    asyncio.run(play(steps, agents, log, limits, SETTINGS.phrases, scale))
    return compute(log.events, scale, limits.ordering)


@settings(max_examples=50, deadline=None)
@given(seed=seeds, size=sizes)
def test_invariants_hold_in_strict_mode(seed: int, size: int):
    limits = _limits(ordering=Ordering.STRICT, status_after_s=SCALE)
    m = _run(random_scenario(POOLS, seed, size), limits)
    assert m.invariants_ok, m.problems


@settings(max_examples=50, deadline=None)
@given(seed=seeds, size=sizes)
def test_invariants_hold_in_bounded_mode(seed: int, size: int):
    limits = _limits(ordering=Ordering.BOUNDED, max_hold_s=SCALE, status_after_s=SCALE)
    m = _run(random_scenario(POOLS, seed, size), limits)
    assert m.invariants_ok, m.problems


@settings(max_examples=30, deadline=None)
@given(seed=seeds, size=sizes)
def test_invariants_hold_in_reply_mode(seed: int, size: int):
    m = _run(random_scenario(POOLS, seed, size), _limits(ordering=Ordering.REPLY))
    assert m.invariants_ok, m.problems


@settings(max_examples=30, deadline=None)
@given(seed=seeds, size=sizes)
def test_invariants_hold_in_the_serial_baseline(seed: int, size: int):
    m = _run(random_scenario(POOLS, seed, size), _limits().as_serial())
    assert m.invariants_ok, m.problems


def test_parallel_beats_serial_on_the_busy_scenario():
    steps = load_scenarios(SETTINGS.scenarios_dir)["busy"].steps
    serial = _run(steps, _limits().as_serial(), scale=0.02)
    parallel = _run(steps, _limits(), scale=0.02)
    assert serial.invariants_ok and parallel.invariants_ok
    assert parallel.latency_mean_s < serial.latency_mean_s
    assert parallel.makespan_s < serial.makespan_s


@settings(max_examples=50, deadline=None)
@given(seed=seeds, size=sizes)
def test_random_replies_only_point_at_earlier_messages_in_the_same_chat(seed: int, size: int):
    sent: dict[str, int] = {}
    for step in random_scenario(POOLS, seed, size):
        if step.reply_to:
            assert 1 <= int(step.reply_to.removeprefix("m")) <= sent.get(step.chat, 0)
        sent[step.chat] = sent.get(step.chat, 0) + 1
