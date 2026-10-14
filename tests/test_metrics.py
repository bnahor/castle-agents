from __future__ import annotations

from typing import Any

import pytest

from castle_agents.observability import INVARIANTS, compute
from castle_agents.runtime import Ordering

Event = dict[str, Any]


def ev(type: str, t: float = 0.0, chat: str = "c", **data: Any) -> Event:
    return {"t": t, "chat": chat, "type": type, **data}


def asked(seq: int, version: int = 1) -> list[Event]:
    return [
        ev("inbound", msg_id=f"m{seq}"),
        ev("admit", request=f"c#{seq}", seq=seq, msg_id=f"m{seq}", version=version),
        ev("running", request=f"c#{seq}", version=version, included=[]),
        ev("done", request=f"c#{seq}", version=version),
    ]


def posted(seq: int, version: int = 1, early: bool = False, outcome: str = "answered") -> Event:
    return ev(
        "outbound",
        1.0,
        kind="answer",
        seq=seq,
        request=f"c#{seq}",
        version=version,
        early=early,
        outcome=outcome,
    )


def broken(events: list[Event], ordering: Ordering = Ordering.STRICT) -> dict[str, int]:
    metrics = compute(events, ordering=ordering)
    return {name: getattr(metrics, name) for name in INVARIANTS if getattr(metrics, name)}


def test_a_clean_log_breaks_nothing():
    assert broken([*asked(1), *asked(2), posted(1), posted(2)]) == {}


def test_an_answer_from_a_superseded_version_is_stale():
    events = [*asked(1), ev("restart", request="c#1", version=2), posted(1, version=1)]
    assert broken(events) == {"stale_posts": 1}


def test_an_answer_for_a_cancelled_request_is_stale():
    events = [*asked(1), ev("cancel", request="c#1", version=2), posted(1, version=1)]
    assert broken(events) == {"stale_posts": 1}


def test_identical_additions_are_tracked_separately():
    events = [
        *asked(1)[:2],
        ev("addition_accepted", request="c#1", addition="m2", text="include INTC"),
        ev("addition_accepted", request="c#1", addition="m3", text="include INTC"),
        ev("running", request="c#1", version=1, included=[]),
        ev("steer_applied", request="c#1", version=1, addition="m2", text="include INTC"),
        ev("done", request="c#1", version=1),
        posted(1),
    ]
    assert broken(events) == {"lost_steers": 1}


def test_an_addition_applied_only_to_a_discarded_generation_is_lost():
    events = [
        *asked(1)[:3],
        ev("addition_accepted", request="c#1", addition="m2", text="include INTC"),
        ev("steer_applied", request="c#1", version=1, addition="m2", text="include INTC"),
        ev("restart", request="c#1", version=2),
        ev("running", request="c#1", version=2, included=[]),
        ev("done", request="c#1", version=2),
        posted(1, version=2),
    ]
    assert broken(events) == {"lost_steers": 1}


def test_an_addition_in_the_delivered_generations_scope_is_not_lost():
    events = [
        *asked(1)[:2],
        ev("addition_accepted", request="c#1", addition="m2", text="include INTC"),
        ev("restart", request="c#1", version=2),
        ev("running", request="c#1", version=2, included=["m2"]),
        ev("done", request="c#1", version=2),
        posted(1, version=2),
    ]
    assert broken(events) == {}


def test_a_slot_that_is_never_posted_is_missing():
    assert broken([*asked(1), *asked(2), posted(1)]) == {"missing_posts": 1}


def test_a_slot_posted_twice_is_a_duplicate():
    assert broken([*asked(1), posted(1), posted(1)]) == {"duplicate_posts": 1}


def test_posting_out_of_order_violates_strict_ordering():
    assert broken([*asked(1), *asked(2), posted(2), posted(1)]) == {"order_violations": 1}


@pytest.mark.parametrize(
    ("ordering", "violations"),
    [(Ordering.BOUNDED, 0), (Ordering.STRICT, 1)],
)
def test_an_early_release_is_legal_only_in_bounded_mode(ordering, violations):
    events = [*asked(1), *asked(2), posted(2, early=True), posted(1)]
    assert broken(events, ordering).get("order_violations", 0) == violations


def test_a_failed_answer_drops_its_additions_instead_of_losing_them():
    events = [
        *asked(1),
        ev("addition_accepted", request="c#1", addition="m2", text="include INTC"),
        posted(1, outcome="timed_out"),
    ]
    metrics = compute(events)
    assert broken(events) == {}
    assert (metrics.answers, metrics.failed_answers, metrics.additions_dropped) == (0, 1, 1)
