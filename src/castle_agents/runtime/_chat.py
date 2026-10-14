from __future__ import annotations

import asyncio
import itertools
from collections import deque
from typing import TYPE_CHECKING, Any

from ..domain import (
    Action,
    Addition,
    Exchange,
    Inbound,
    Kind,
    Outbound,
    Outcome,
    PostKind,
    Request,
    RequestView,
    Route,
    Status,
    Steering,
    shorten,
)
from ._executor import Job, Scope, Superseded
from ._orderer import Orderer
from ._ports import OnEvent

if TYPE_CHECKING:
    from ._dispatcher import Dispatcher


class ChatRuntime:
    def __init__(self, chat_id: str, dispatcher: Dispatcher) -> None:
        self.id = chat_id
        self.d = dispatcher
        self.limits = dispatcher.limits
        self.requests: dict[str, Request] = {}
        self.history: deque[Exchange] = deque(maxlen=self.limits.history_window)
        self._by_msg: dict[str, Request] = {}
        self._speculations: dict[str, asyncio.Task[str]] = {}
        self._phrases = dispatcher.phrases
        self._seqs = itertools.count(1)
        self._queue: asyncio.Queue[Inbound] = asyncio.Queue()
        self._routing = False
        self._lane = asyncio.Semaphore(1 if self.limits.serial else self.limits.heavy_per_chat)
        self.orderer = Orderer(
            chat_id, self._post, dispatcher.sink.now, self.limits, dispatcher.phrases
        )
        self._route_task = asyncio.create_task(self._route_loop(), name=f"route:{chat_id}")

    def submit(self, msg: Inbound) -> None:
        self._log("inbound", msg_id=msg.msg_id, user=msg.user, text=msg.text, reply_to=msg.reply_to)
        if self.limits.speculate_quick:
            self._speculations[msg.msg_id] = self.d.executor.speculate(
                msg.text, tuple(self.history)
            )
        self._queue.put_nowait(msg)

    async def settle(self) -> bool:
        had_work = self._routing or not self._queue.empty()
        await self._queue.join()
        running = [r.task for r in self.requests.values() if r.task and not r.task.done()]
        await asyncio.gather(*running, return_exceptions=True)
        return had_work or bool(running)

    async def close(self) -> None:
        self.orderer.close()
        tasks: list[asyncio.Task[Any]] = [self._route_task, *self._speculations.values()]
        for request in self.requests.values():
            tasks += [task for task in (request.task, request.head_start) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # Routing is serial within a chat so slots follow arrival order and every edit is
    # routed against an up-to-date view. Messages that arrive meanwhile are routed together.
    async def _route_loop(self) -> None:
        while True:
            batch = await self._next_batch()
            self._routing = True
            try:
                await self._handle(batch)
            finally:
                self._routing = False
                for _ in batch:
                    self._queue.task_done()

    async def _next_batch(self) -> list[Inbound]:
        batch = [await self._queue.get()]
        while len(batch) < self.limits.route_batch_max and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        return batch

    async def _handle(self, batch: list[Inbound]) -> None:
        routes = await self._route(batch)
        for msg in batch:
            try:
                self._apply(msg, routes[msg.msg_id])
            except Exception as e:
                self._log("handle_error", msg_id=msg.msg_id, error=repr(e))
                self._discard_speculation(msg.msg_id)
                self._reply(msg, self._phrases.not_processed)

    async def _route(self, batch: list[Inbound]) -> dict[str, Route]:
        try:
            async with asyncio.timeout(self.limits.router_deadline_s):
                routes = await self.d.agents.router.route(batch, self._context(batch))
        except Exception as e:
            self._log("router_error", msgs=[m.msg_id for m in batch], error=repr(e))
            routes = []
        self._log("routed_batch", size=len(batch))
        decided = {route.msg_id: route for route in routes}
        return {msg.msg_id: decided.get(msg.msg_id) or _fallback(msg) for msg in batch}

    def _apply(self, msg: Inbound, route: Route) -> None:
        route, target = self._resolve(msg, route)
        self._log_route(msg, route)
        speculation = self._speculations.pop(msg.msg_id, None)
        if target is None:
            head_start = speculation if route.complexity is Kind.SIMPLE else None
            if speculation and head_start is None:
                speculation.cancel()
            self._admit(msg, route.text or msg.text, route.complexity, head_start)
            return
        if speculation:
            speculation.cancel()
        if target.status.closed:
            self._follow_up(msg, target, route)
        else:
            self._remember(msg, target)
            self._edit(msg, target, route)

    def _resolve(self, msg: Inbound, route: Route) -> tuple[Route, Request | None]:
        if route.action is Action.NEW:
            return route, None
        key = route.target or ""
        target = self.requests.get(key) or self._by_msg.get(key)
        if target is None:
            self._log("route_invalid", msg_id=msg.msg_id, target=route.target)
            reason = f"unknown target {route.target!r}"
            return Route.new(msg.msg_id, msg.text, route.complexity, reason), None
        return route, target

    def _edit(self, msg: Inbound, request: Request, route: Route) -> None:
        match route.action:
            case Action.CANCEL:
                self._cancel(msg, request)
            case Action.AMEND_RESTART:
                self._rewrite(msg, request, route.text)
            case Action.AMEND_STEER:
                self._steer(msg, request, route.text)
            case Action.NEW:
                raise AssertionError("new requests have no target")

    def _follow_up(self, msg: Inbound, target: Request, route: Route) -> None:
        if route.action is Action.CANCEL:
            closed = self._phrases.already_closed.format(seq=target.seq, status=target.status)
            self._ack(msg, target, closed)
            return
        if route.action is Action.AMEND_RESTART:
            text = route.text
        else:
            original = self._effective_text(target)
            text = self._phrases.follow_up.format(original=original, addition=route.text)
        self._admit(msg, text, Kind.HEAVY)

    def _cancel(self, msg: Inbound, request: Request) -> None:
        self._drop(request)
        self._ack(msg, request, self._phrases.ack_cancel.format(seq=request.seq))

    def _rewrite(self, msg: Inbound, request: Request, text: str) -> None:
        # The router saw the request with its additions and restated all of it, so the
        # rewrite supersedes them rather than having them appended a second time.
        superseded = [addition.id for addition in request.additions]
        request.text = text or self._effective_text(request)
        request.additions.clear()
        self._log("additions_superseded", request=request.id, additions=superseded)
        self._restart(request, why="rewritten")
        preview = shorten(self._effective_text(request), self._phrases.preview_chars)
        self._ack(msg, request, self._phrases.ack_rewrite.format(seq=request.seq, preview=preview))

    def _steer(self, msg: Inbound, request: Request, text: str) -> None:
        addition = Addition(msg.msg_id, text)
        request.additions.append(addition)
        self._log("addition_accepted", request=request.id, addition=addition.id, text=text)
        if request.status is Status.RUNNING and request.kind is Kind.HEAVY:
            request.steering.push(addition)
        else:
            self._restart(request, why="addition to a request that was not mid-tool-loop")
        self._ack(msg, request, self._phrases.ack_steer.format(seq=request.seq))

    def _admit(
        self, msg: Inbound, text: str, kind: Kind, head_start: asyncio.Task[str] | None = None
    ) -> None:
        seq = next(self._seqs)
        request = Request(
            id=f"{self.id}#{seq}",
            chat_id=self.id,
            seq=seq,
            kind=kind,
            text=text,
            user=msg.user,
            msg_ids=[],
            head_start=head_start,
        )
        self.requests[request.id] = request
        self._remember(msg, request)
        self.orderer.reserve(seq, request.id, text, msg.msg_id)
        self._log(
            "admit",
            request=request.id,
            seq=seq,
            kind=kind,
            text=text,
            msg_id=msg.msg_id,
            version=request.version,
            head_start=head_start is not None,
        )
        self._start(request)

    def _remember(self, msg: Inbound, request: Request) -> None:
        request.msg_ids.append(msg.msg_id)
        self._by_msg[msg.msg_id] = request

    def _start(self, request: Request) -> None:
        request.status = Status.QUEUED
        request.task = asyncio.create_task(self._work(request, request.version), name=request.id)

    def _restart(self, request: Request, why: str) -> None:
        if request.status is Status.QUEUED and request.head_start is None:
            self._amend_in_queue(request)
            return
        request.version += 1
        request.steering = Steering()
        request.cancel_task()
        self.orderer.retract(request.seq, preview=self._effective_text(request))
        self._log("restart", request=request.id, version=request.version, why=why)
        self._start(request)

    def _amend_in_queue(self, request: Request) -> None:
        # The worker reads its scope when it starts, so the edit lands without losing its place.
        self.orderer.retract(request.seq, preview=self._effective_text(request))
        self._log("amended_in_queue", request=request.id)

    def _effective_text(self, request: Request) -> str:
        if not request.additions:
            return request.text
        additions = "; ".join(addition.text for addition in request.additions)
        return self._phrases.fold.format(original=request.text, addition=additions)

    def _scope(self, request: Request) -> Scope:
        return Scope(self._effective_text(request), tuple(a.id for a in request.additions))

    def _drop(self, request: Request) -> None:
        request.version += 1
        request.cancel_task()
        request.status = Status.CANCELLED
        self._log("cancel", request=request.id, version=request.version)
        self.orderer.retract(request.seq)
        self.orderer.complete(request.seq, "", kind=PostKind.SKIP)
        self._prune()

    def _discard_speculation(self, msg_id: str) -> None:
        if speculation := self._speculations.pop(msg_id, None):
            speculation.cancel()

    async def _work(self, request: Request, version: int) -> None:
        note = self._noter(request, version)
        try:
            job = Job(
                request,
                version,
                self._lane,
                tuple(self.history),
                note,
                lambda: self._scope(request),
            )
            answer = await self.d.executor.run(job)
        except Superseded:
            return
        except asyncio.CancelledError:
            note("worker_cancelled")
            raise
        except TimeoutError:
            note("worker_timed_out")
            answer, outcome = self._phrases.timed_out.format(seq=request.seq), Outcome.TIMED_OUT
        except Exception as e:
            note("worker_error", error=repr(e))
            answer, outcome = self._phrases.apology.format(seq=request.seq), Outcome.FAILED
        else:
            outcome = Outcome.ANSWERED
        self._finish(request, version, answer, outcome, note)

    def _finish(
        self, request: Request, version: int, answer: str, outcome: Outcome, note: OnEvent
    ) -> None:
        # Staleness is decided here, not by cancellation: a worker that ignores
        # cancel still cannot post an answer for a version that is no longer current.
        if not request.is_current(version):
            note("stale_dropped", current=request.version)
            return
        request.status = Status.DONE
        note("done", outcome=outcome, text=answer)
        self.orderer.complete(request.seq, answer, outcome=outcome)

    def _noter(self, request: Request, version: int) -> OnEvent:
        def note(event: str, **data: Any) -> None:
            self._log(event, request=request.id, version=version, **data)

        return note

    def _ack(self, msg: Inbound, request: Request, text: str) -> None:
        self._post(
            Outbound(self.id, PostKind.ACK, text, request_id=request.id, reply_to=msg.msg_id)
        )

    def _reply(self, msg: Inbound, text: str) -> None:
        self._post(Outbound(self.id, PostKind.ACK, text, reply_to=msg.msg_id))

    def _post(self, out: Outbound) -> None:
        request = self.requests.get(out.request_id or "")
        delivered = request is not None and out.kind is PostKind.ANSWER and out.fills_slot
        if request and delivered:
            request.status = Status.DELIVERED
            self.history.append(Exchange(self._effective_text(request), out.text))
        self._log(
            "outbound",
            kind=out.kind,
            seq=out.seq,
            request=out.request_id,
            version=request.version if request else None,
            reply_to=out.reply_to,
            early=out.released_early,
            outcome=out.outcome,
            text=out.text,
        )
        if out.kind is not PostKind.SKIP:
            self.d.emit(out)
        if delivered:
            self._prune()

    def _prune(self) -> None:
        closed = [r for r in self.requests.values() if r.status.closed]
        for request in closed[: max(0, len(closed) - self.limits.retain_closed)]:
            del self.requests[request.id]
            for msg_id in request.msg_ids:
                self._by_msg.pop(msg_id, None)

    def _log_route(self, msg: Inbound, route: Route) -> None:
        self._log(
            "route",
            msg_id=msg.msg_id,
            action=route.action,
            complexity=route.complexity,
            target=route.target,
            text=route.text,
            reason=route.reason,
        )

    def _context(self, batch: list[Inbound]) -> list[RequestView]:
        recent = list(self.requests.values())[-self.limits.recent_window :]
        active = [r for r in self.requests.values() if not r.status.closed]
        replied = [self._by_msg[m.reply_to] for m in batch if m.reply_to in self._by_msg]
        shown = {r.id: r for r in (*recent, *active, *replied)}
        ordered = sorted(shown.values(), key=lambda r: r.seq)
        return [RequestView.of(r, self._effective_text(r)) for r in ordered]

    def _log(self, type: str, **data: Any) -> None:
        self.d.sink.append(self.id, type, **data)


def _fallback(msg: Inbound) -> Route:
    return Route.new(msg.msg_id, msg.text, Kind.HEAVY, "router gave no decision; heavy is safe")
