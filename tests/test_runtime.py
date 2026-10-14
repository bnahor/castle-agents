from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from castle_agents.domain import Action, Inbound, Kind, RequestView, Route, Steering
from castle_agents.observability import compute
from castle_agents.runtime import History, OnEvent, Ordering

from .conftest import DelayedRouter, instant_router

HEAVY = "Analyze NVDA vs AMD margins"
SIMPLE = "what does ETF stand for?"
QQQ = "Analyze the top holdings of QQQ"
SPY = "Analyze the top holdings of SPY"
TSLA = "Why did TSLA drop?"


async def test_strict_mode_posts_answers_in_ask_order_even_when_they_finish_out_of_order(
    make_harness,
):
    h = make_harness(ordering="strict")
    h.say(HEAVY)
    h.say(SIMPLE, user="bob")
    await h.routed()
    h.release(SIMPLE)
    await asyncio.sleep(0.05)
    assert h.slot_posts() == []
    h.release(HEAVY)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"research:{HEAVY}", f"quick:{SIMPLE}"]
    assert compute(h.log.events, ordering=Ordering.STRICT).invariants_ok


async def test_bounded_mode_releases_a_held_answer_as_a_reply_after_max_hold(make_harness):
    h = make_harness(ordering="bounded", max_hold_s=0.05)
    h.say(HEAVY)
    m2 = h.say(SIMPLE, user="bob")
    await h.routed()
    h.release(SIMPLE)
    await asyncio.sleep(0.01)
    assert h.slot_posts() == []
    await asyncio.sleep(0.1)
    assert [(p.seq, p.reply_to, p.released_early) for p in h.slot_posts()] == [(2, m2, True)]
    h.release(HEAVY)
    await h.idle()
    assert [p.seq for p in h.slot_posts()] == [2, 1]
    assert compute(h.log.events, ordering=Ordering.BOUNDED).invariants_ok


async def test_reply_mode_posts_each_answer_as_soon_as_it_is_ready(make_harness):
    h = make_harness(ordering="reply")
    h.say(HEAVY)
    m2 = h.say(SIMPLE, user="bob")
    await h.routed()
    h.release(SIMPLE)
    await h.until(lambda: bool(h.slot_posts()))
    assert [(p.seq, p.reply_to) for p in h.slot_posts()] == [(2, m2)]
    h.release(HEAVY)
    await h.idle()
    assert [p.seq for p in h.slot_posts()] == [2, 1]


async def test_rewrite_restarts_the_work_in_the_same_slot_and_never_posts_the_old_answer(
    make_harness,
):
    h = make_harness()
    h.say(QQQ)
    await h.routed()
    h.say("actually make that SPY instead of QQQ")
    await h.routed()
    h.release(QQQ, SPY)
    await h.idle()
    assert ("research", QQQ) in h.scripted.cancelled
    assert [(p.seq, p.text) for p in h.slot_posts()] == [(1, f"research:{SPY}")]


async def test_steer_reaches_the_running_worker_without_restarting_it(make_harness):
    h = make_harness()
    h.say(HEAVY)
    await h.routed()
    h.say("also include INTC")
    await h.routed()
    h.release(HEAVY)
    await h.idle()
    assert h.scripted.research_calls == [HEAVY]
    assert h.slot_posts()[0].text == f"research:{HEAVY} +include INTC"


async def test_an_edit_to_a_queued_request_is_applied_before_it_starts(make_harness):
    edited = "Summarize AAPL earnings risk; also: dividends"
    h = make_harness(heavy_per_chat=1)
    h.say(HEAVY)
    m2 = h.say("Summarize AAPL earnings risk", user="bob")
    await h.routed()
    h.say("add dividends", user="bob", reply_to=m2)
    await h.routed()
    h.release(HEAVY, edited)
    await h.idle()
    assert h.scripted.research_calls == [HEAVY, edited]
    assert [p.text for p in h.slot_posts()] == [f"research:{HEAVY}", f"research:{edited}"]


async def test_cancel_stops_the_work_and_unblocks_later_answers(make_harness):
    h = make_harness(ordering="strict")
    h.say(TSLA)
    h.say(SIMPLE, user="bob")
    await h.routed()
    h.release(SIMPLE)
    h.say("nevermind the TSLA thing")
    await h.idle()
    assert ("research", TSLA) in h.scripted.cancelled
    assert [(p.seq, p.text) for p in h.slot_posts()] == [(2, f"quick:{SIMPLE}")]
    assert [p.text for p in h.posts if p.kind == "ack"] == ["Okay, dropping #1."]


async def test_an_edit_after_delivery_becomes_a_follow_up_request(make_harness):
    follow_up = f"{SIMPLE} — follow-up: explain ETNs"
    h = make_harness()
    m1 = h.say(SIMPLE)
    h.release(SIMPLE, follow_up)
    await h.idle()
    h.say("also explain ETNs", reply_to=m1)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"quick:{SIMPLE}", f"research:{follow_up}"]


async def test_a_quick_question_that_needs_data_is_answered_by_research_in_its_slot(
    make_harness,
):
    question = "what's the latest price of MSFT?"
    h = make_harness()
    h.say(question)
    h.release(question)
    await h.idle()
    assert h.scripted.quick_calls == [question]
    assert [(p.seq, p.text) for p in h.slot_posts()] == [(1, f"research:{question}")]


class _BrokenRouter:
    async def route(self, batch: Sequence[Inbound], recent: list[RequestView]) -> list[Route]:
        raise RuntimeError("router down")


async def test_a_router_failure_still_answers_via_research(make_harness):
    h = make_harness(router=_BrokenRouter())
    h.say(SIMPLE)
    h.release(SIMPLE)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"research:{SIMPLE}"]


class _HallucinatingRouter:
    async def route(self, batch: Sequence[Inbound], recent: list[RequestView]) -> list[Route]:
        return [
            Route(
                msg_id=m.msg_id,
                action=Action.AMEND_STEER,
                complexity=Kind.SIMPLE,
                target="c#99",
                text=m.text,
            )
            for m in batch
        ]


async def test_an_edit_aimed_at_an_unknown_request_is_answered_as_a_new_question(make_harness):
    h = make_harness(router=_HallucinatingRouter())
    h.say(SIMPLE)
    h.release(SIMPLE)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"quick:{SIMPLE}"]


async def test_a_status_post_explains_a_long_head_of_line_wait(make_harness):
    h = make_harness(ordering="strict", status_after_s=0.05)
    h.say(HEAVY)
    h.say(SIMPLE, user="bob")
    await h.routed()
    h.release(SIMPLE)
    await h.until(lambda: any(p.kind == "status" for p in h.posts))
    status = [p for p in h.posts if p.kind == "status"]
    assert len(status) == 1 and "#1" in status[0].text
    h.release(HEAVY)
    await h.idle()


async def test_a_slow_chat_does_not_block_another_chat(make_harness):
    h = make_harness()
    h.say(HEAVY, chat="a")
    h.say(SIMPLE, chat="b")
    h.release(SIMPLE)
    await h.until(lambda: bool(h.slot_posts()))
    assert [(p.chat_id, p.seq) for p in h.slot_posts()] == [("b", 1)]
    h.release(HEAVY)
    await h.idle()


STUBBORNNESS = 3


async def _wait_ignoring_cancellation(event: asyncio.Event) -> None:
    """Like some client libraries: swallow a few cancellations before giving in."""
    for _ in range(STUBBORNNESS):
        try:
            await event.wait()
            return
        except asyncio.CancelledError:
            continue
    await event.wait()


class _IgnoresCancellation:
    def __init__(self) -> None:
        self.go = asyncio.Event()

    async def research(
        self, text: str, history: History, steering: Steering, on_event: OnEvent
    ) -> str:
        await _wait_ignoring_cancellation(self.go)
        return f"research:{text}"


async def test_a_worker_that_ignores_cancellation_still_cannot_post_a_stale_answer(
    make_harness,
):
    stubborn = _IgnoresCancellation()
    h = make_harness(researcher=stubborn)
    try:
        h.say(QQQ)
        await h.routed()
        h.say("actually make that SPY instead of QQQ")
        await h.routed()
    finally:
        stubborn.go.set()
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"research:{SPY}"]
    assert compute(h.log.events).invariants_ok


ROUTER_DELAY_S = 0.1


@pytest.mark.parametrize(("batch_max", "within_s"), [(8, 1.6), (1, None)])
async def test_messages_that_arrive_together_are_routed_together(
    make_harness, settings, batch_max, within_s
):
    router = DelayedRouter(instant_router(settings), ROUTER_DELAY_S)
    h = make_harness(router=router, route_batch_max=batch_max)
    h.release(SPY, SIMPLE)
    h.say(QQQ)
    h.say("actually make that SPY instead of QQQ", user="cara")
    h.say(SIMPLE, user="bob")
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"research:{SPY}", f"quick:{SIMPLE}"]
    finished = max(h.posted_at) / ROUTER_DELAY_S
    if within_s is not None:
        assert finished < within_s
    else:
        assert finished >= 2.5


@pytest.mark.parametrize(("speculate", "faster"), [(True, True), (False, False)])
async def test_a_quick_answer_starts_while_the_message_is_still_being_routed(
    make_harness, settings, speculate, faster
):
    router = DelayedRouter(instant_router(settings), ROUTER_DELAY_S)
    h = make_harness(router=router, quick_delay_s=ROUTER_DELAY_S, speculate_quick=speculate)
    h.say(SIMPLE)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"quick:{SIMPLE}"]
    sequential = 2 * ROUTER_DELAY_S
    assert (h.posted_at[-1] < 0.8 * sequential) is faster


async def test_a_speculative_quick_answer_is_cancelled_when_the_question_is_heavy(make_harness):
    h = make_harness()
    h.say(HEAVY)
    await h.routed()
    assert ("quick", HEAVY) in h.scripted.cancelled
    h.release(HEAVY)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"research:{HEAVY}"]


async def test_close_leaves_no_pending_tasks(make_harness):
    h = make_harness()
    h.say(HEAVY)
    h.say(SIMPLE, user="bob")
    await h.routed()
    await h.d.close()
    current = asyncio.current_task()
    assert [t for t in asyncio.all_tasks() if t is not current and not t.done()] == []


async def test_an_applied_addition_survives_a_later_addition_that_restarts_the_work(make_harness):
    with_intc = f"research:{QQQ} +include INTC"
    both = f"{QQQ}; also: include INTC; and what about AVGO?"
    h = make_harness(ordering="strict")
    h.say(HEAVY)
    m2 = h.say(QQQ, user="cara")
    await h.routed()
    h.say("also include INTC", user="cara", reply_to=m2)
    await h.routed()
    h.release(QQQ)
    await h.until(lambda: h.scripted.research_calls.count(QQQ) == 1 and QQQ in h.scripted.gates)
    await asyncio.sleep(0.01)
    h.say("and what about AVGO?", user="cara", reply_to=m2)
    await h.routed()
    h.release(HEAVY, both)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"research:{HEAVY}", f"research:{both}"]
    assert with_intc not in [p.text for p in h.posts]
    assert compute(h.log.events, ordering=Ordering.STRICT).invariants_ok


async def test_a_rewrite_keeps_additions_accepted_before_it(make_harness):
    rewritten = f"{SPY}; also: include INTC"
    h = make_harness()
    h.say(QQQ)
    await h.routed()
    h.say("also include INTC")
    await h.routed()
    h.say("actually make that SPY instead of QQQ")
    await h.routed()
    h.release(rewritten)
    await h.idle()
    assert [p.text for p in h.slot_posts()] == [f"research:{rewritten}"]
    assert compute(h.log.events).invariants_ok


async def test_a_follow_up_to_an_amended_answer_carries_its_additions(make_harness):
    follow_up = f"{QQQ}; also: include INTC — follow-up: and what about AVGO?"
    h = make_harness()
    m1 = h.say(QQQ)
    await h.routed()
    h.say("also include INTC")
    await h.routed()
    h.release(QQQ, follow_up)
    await h.idle()
    h.say("and what about AVGO?", reply_to=m1)
    await h.idle()
    assert h.slot_posts()[-1].text == f"research:{follow_up}"


async def test_a_reply_reaches_its_request_even_outside_the_recent_window(make_harness):
    h = make_harness(recent_window=1)
    m1 = h.say(QQQ)
    h.say(HEAVY, user="bob")
    h.say(TSLA, user="dave")
    await h.routed()
    h.say("add dividends", user="alice", reply_to=m1)
    await h.routed()
    acks = [p.text for p in h.posts if p.kind == "ack"]
    assert acks == ["Adding that to #1."]
    h.release(QQQ, HEAVY, TSLA)
    await h.idle()


async def test_a_request_past_its_deadline_times_out_and_unblocks_others(make_harness):
    h = make_harness(ordering="strict", request_deadline_s=0.1)
    h.say(HEAVY)
    h.say(SIMPLE, user="bob")
    h.release(SIMPLE)
    await h.routed()
    h.say("also include INTC")
    await h.idle()
    first, second = h.slot_posts()
    assert (first.outcome, first.text.startswith("Sorry, #1 took too long")) == ("timed_out", True)
    assert (second.outcome, second.text) == ("answered", f"quick:{SIMPLE}")
    m = compute(h.log.events, ordering=Ordering.STRICT)
    assert (m.answers, m.failed_answers, m.additions_dropped) == (1, 1, 1)
    assert m.invariants_ok


class _Crashes:
    async def research(
        self, text: str, history: History, steering: Steering, on_event: OnEvent
    ) -> str:
        raise ConnectionError("provider unreachable")


async def test_a_worker_error_is_reported_as_a_failure_not_an_answer(make_harness):
    h = make_harness(researcher=_Crashes())
    h.say(HEAVY)
    await h.idle()
    (post,) = h.slot_posts()
    assert post.outcome == "failed" and post.text.startswith("Sorry, I hit an error on #1")
    m = compute(h.log.events)
    assert (m.answers, m.failed_answers, m.latency_mean_s) == (0, 1, 0.0)


async def test_a_lone_long_request_gets_one_progress_post(make_harness):
    h = make_harness(ordering="strict", status_after_s=0.05)
    h.say(HEAVY)
    await h.until(lambda: any(p.kind == "status" for p in h.posts))
    await asyncio.sleep(0.1)
    status = [p.text for p in h.posts if p.kind == "status"]
    assert len(status) == 1 and status[0].startswith("Still working on #1")
    h.release(HEAVY)
    await h.idle()


@pytest.mark.parametrize(("batch_max", "max_latency_routers"), [(1, 3), (8, 1)])
async def test_reported_latency_includes_waiting_to_be_routed(
    make_harness, settings, batch_max, max_latency_routers
):
    router = DelayedRouter(instant_router(settings), ROUTER_DELAY_S)
    h = make_harness(router=router, route_batch_max=batch_max, quick_delay_s=0)
    for question in ("what is a bond?", "what is a stock?", "what is a fund?"):
        h.say(question)
    await h.idle()
    reported = compute(h.log.events).latency_max_s / ROUTER_DELAY_S
    assert max_latency_routers - 0.05 <= reported < max_latency_routers + 0.6


async def test_a_request_that_just_became_head_of_line_gets_no_immediate_status(make_harness):
    h = make_harness(ordering="strict", status_after_s=0.1)
    h.say(HEAVY)
    h.say(QQQ, user="cara")
    await h.until(lambda: any(p.kind == "status" for p in h.posts))
    h.release(HEAVY)
    await h.until(lambda: bool(h.slot_posts()))
    await asyncio.sleep(0.05)
    assert [p.text for p in h.posts if p.kind == "status"] == [
        "Still working on #1 (Analyze NVDA vs AMD margins)."
    ]
    await h.until(lambda: sum(p.kind == "status" for p in h.posts) == 2)
    h.release(QQQ)
    await h.idle()


async def test_additions_past_the_limit_are_refused_with_a_receipt(make_harness):
    h = make_harness(max_additions=1)
    h.say(HEAVY)
    await h.routed()
    h.say("also include INTC")
    h.say("also include AVGO")
    await h.routed()
    acks = [p.text for p in h.posts if p.kind == "ack"]
    assert acks == [
        "Adding that to #1.",
        "#1 is at its addition limit (1), so I left that one out. Ask it as a new question?",
    ]
    h.release(HEAVY)
    await h.idle()
    assert h.slot_posts()[0].text == f"research:{HEAVY} +include INTC"


async def test_an_overlong_addition_is_refused_and_the_work_continues(make_harness):
    h = make_harness(max_addition_chars=20)
    h.say(HEAVY)
    await h.routed()
    h.say("also " + "include every semiconductor company you can find")
    await h.routed()
    assert [p.text for p in h.posts if p.kind == "ack"] == [
        "That's too long to add to #1. Could you send it as a new question?"
    ]
    h.release(HEAVY)
    await h.idle()
    assert h.scripted.research_calls == [HEAVY]
    assert h.slot_posts()[0].text == f"research:{HEAVY}"


class _KeepsEveryInbox:
    def __init__(self) -> None:
        self.inboxes: list[Steering] = []
        self.go = asyncio.Event()

    async def research(
        self, text: str, history: History, steering: Steering, on_event: OnEvent
    ) -> str:
        self.inboxes.append(steering)
        await _wait_ignoring_cancellation(self.go)
        return f"research:{text}"


async def test_an_addition_reaches_only_the_current_generations_worker(make_harness):
    worker = _KeepsEveryInbox()
    h = make_harness(researcher=worker)
    try:
        h.say(QQQ)
        await h.routed()
        h.say("actually make that SPY instead of QQQ")
        await h.until(lambda: len(worker.inboxes) == 2)
        h.say("also include INTC")
        await h.routed()
        old, current = worker.inboxes[:2]
        assert old.drain() == []
        assert [a.text for a in current.drain()] == ["include INTC"]
    finally:
        worker.go.set()
    await h.idle()


class _RewritingRouter:
    async def route(self, batch: Sequence[Inbound], recent: list[RequestView]) -> list[Route]:
        return [
            Route(
                msg_id=m.msg_id,
                action=Action.NEW,
                complexity=Kind.SIMPLE,
                text="Explain what an ETF is",
            )
            for m in batch
        ]


@pytest.mark.parametrize("speculate", [True, False])
async def test_a_rewritten_question_is_answered_as_rewritten_never_from_the_raw_text(
    make_harness, speculate
):
    h = make_harness(router=_RewritingRouter(), quick_delay_s=0.01, speculate_quick=speculate)
    h.say("what does that mean?")
    await h.idle()
    assert [p.text for p in h.slot_posts()] == ["quick:Explain what an ETF is"]
