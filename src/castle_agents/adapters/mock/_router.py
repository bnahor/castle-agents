from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence

from ...domain import Action, Inbound, Kind, RequestView, Route, Status
from ._config import MockConfig, anywhere, leading

TICKER = re.compile(r"\b[A-Z]{2,5}\b")
_SWAP = re.compile(r"\b([A-Z]{2,5})\s+instead of\s+([A-Z]{2,5})\b")


class MockRouter:
    def __init__(self, config: MockConfig) -> None:
        patterns = config.patterns
        self._latency = config.router_latency_s
        self._cancel = leading(patterns.cancel)
        self._rewrite = leading(patterns.rewrite)
        self._steer = re.compile(leading(patterns.steer).pattern + r"\b[,:]?\s*", re.IGNORECASE)
        self._heavy = anywhere(patterns.heavy)

    async def route(self, batch: Sequence[Inbound], recent: list[RequestView]) -> list[Route]:
        await asyncio.sleep(self._latency)
        views = list(recent)
        routes: list[Route] = []
        for msg in batch:
            route = self._route_one(msg, views)
            routes.append(route)
            if route.action is Action.NEW:
                views.append(_pending_view(msg, route))
        return routes

    def _route_one(self, msg: Inbound, recent: list[RequestView]) -> Route:
        action = self._classify(msg, recent)
        target = None if action is Action.NEW else _pick_target(msg, recent)
        if target is None:
            return Route.new(msg.msg_id, msg.text, self._complexity(msg.text), "new question")
        return self._amendment(msg.msg_id, action, msg.text, target)

    def _classify(self, msg: Inbound, recent: list[RequestView]) -> Action:
        if self._cancel.match(msg.text):
            return Action.CANCEL
        if self._rewrite.match(msg.text):
            return Action.AMEND_RESTART
        if self._steer.match(msg.text):
            return Action.AMEND_STEER
        if msg.reply_to and any(msg.reply_to in r.msg_ids for r in recent):
            return Action.AMEND_STEER
        return Action.NEW

    def _complexity(self, text: str) -> Kind:
        return Kind.HEAVY if self._heavy.search(text) else Kind.SIMPLE

    def _amendment(self, msg_id: str, action: Action, text: str, target: RequestView) -> Route:
        match action:
            case Action.CANCEL:
                body, complexity = "", Kind.SIMPLE
            case Action.AMEND_RESTART:
                body, complexity = self._rewritten(target.text, text), Kind.HEAVY
            case _:
                body, complexity = self._steer.sub("", text).strip() or text, Kind.HEAVY
        return Route(
            msg_id=msg_id, action=action, complexity=complexity, target=target.id, text=body
        )

    def _rewritten(self, original: str, edit: str) -> str:
        swap = _SWAP.search(edit)
        if swap and swap.group(2) in original:
            return original.replace(swap.group(2), swap.group(1))
        return f"{original} (revised: {self._rewrite.sub('', edit).strip()})"


def _pick_target(msg: Inbound, recent: list[RequestView]) -> RequestView | None:
    if msg.reply_to:
        replied = [r for r in recent if msg.reply_to in r.msg_ids]
        if replied:
            return replied[0]
    live = [r for r in recent if r.status is not Status.CANCELLED]
    open_ = [r for r in live if r.status is not Status.DELIVERED]
    tickers = set(TICKER.findall(msg.text))
    candidates = (
        [r for r in open_ if tickers & set(TICKER.findall(r.text))],
        [r for r in open_ if r.user == msg.user],
        open_,
        [r for r in live if r.user == msg.user],
    )
    return next((pool[-1] for pool in candidates if pool), None)


def _pending_view(msg: Inbound, route: Route) -> RequestView:
    return RequestView(msg.msg_id, 0, msg.user, route.text, Status.QUEUED, (msg.msg_id,))
