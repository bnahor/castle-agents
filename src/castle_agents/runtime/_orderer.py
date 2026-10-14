from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from ..domain import Outbound, Outcome, PostKind, shorten
from ._config import Limits, Ordering, Phrases


@dataclass(slots=True)
class _Slot:
    seq: int
    request_id: str
    preview: str
    reply_to: str | None
    head_since: float
    text: str | None = None
    kind: PostKind = PostKind.ANSWER
    outcome: Outcome = Outcome.ANSWERED
    ready_at: float = 0.0
    released: bool = False
    explained: bool = False

    @property
    def ready(self) -> bool:
        return self.text is not None


class Orderer:
    def __init__(
        self,
        chat_id: str,
        emit: Callable[[Outbound], None],
        clock: Callable[[], float],
        limits: Limits,
        phrases: Phrases,
    ) -> None:
        self.chat_id = chat_id
        self.mode = limits.ordering
        self._max_hold_s = limits.max_hold_s
        self._status_after_s = limits.status_after_s
        self._phrases = phrases
        self._emit = emit
        self._clock = clock
        self._slots: dict[int, _Slot] = {}
        self._next = 1
        self._timer: asyncio.TimerHandle | None = None

    def reserve(self, seq: int, request_id: str, preview: str, reply_to: str | None) -> None:
        self._slots[seq] = _Slot(seq, request_id, preview, reply_to, self._clock())
        if self.mode is not Ordering.REPLY:
            self._tick()

    def complete(
        self,
        seq: int,
        text: str,
        kind: PostKind = PostKind.ANSWER,
        outcome: Outcome = Outcome.ANSWERED,
    ) -> None:
        slot = self._slots[seq]
        slot.text, slot.kind, slot.outcome, slot.ready_at = text, kind, outcome, self._clock()
        if self.mode is Ordering.REPLY:
            self._post(self._slots.pop(seq))
            return
        self._flush()
        self._tick()

    def retract(self, seq: int, preview: str | None = None) -> None:
        if slot := self._slots.get(seq):
            slot.text = None
            slot.preview = preview or slot.preview

    def close(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _flush(self) -> None:
        while (slot := self._slots.get(self._next)) and slot.ready:
            if not slot.released:
                self._post(self._slots[self._next])
            del self._slots[self._next]
            self._next += 1
            if head := self._slots.get(self._next):
                head.head_since = max(head.head_since, self._clock())

    def _tick(self) -> None:
        now = self._clock()
        deadlines = [self._explain_wait(now), *self._release_overdue(now)]
        pending = [deadline for deadline in deadlines if deadline is not None]
        self._arm(min(pending) - now if pending else None)

    def _explain_wait(self, now: float) -> float | None:
        head = self._slots.get(self._next)
        waiting = sum(slot.ready and not slot.released for slot in self._slots.values())
        if head is None or head.ready or head.explained:
            return None
        deadline = head.head_since + self._status_after_s
        if now < deadline:
            return deadline
        head.explained = True
        self._emit(self._status(head, waiting))
        return None

    def _release_overdue(self, now: float) -> list[float]:
        if self.mode is not Ordering.BOUNDED:
            return []
        deadlines: list[float] = []
        for slot in self._slots.values():
            if not slot.ready or slot.released or slot.seq == self._next:
                continue
            deadline = slot.ready_at + self._max_hold_s
            if now >= deadline:
                slot.released = True
                self._post(slot, early=True)
            else:
                deadlines.append(deadline)
        return deadlines

    def _arm(self, delay: float | None) -> None:
        self.close()
        if delay is not None:
            self._timer = asyncio.get_running_loop().call_later(max(delay, 0.0), self._on_timer)

    def _on_timer(self) -> None:
        self._timer = None
        self._tick()

    def _status(self, head: _Slot, waiting: int) -> Outbound:
        templates = {0: self._phrases.status_alone, 1: self._phrases.status_one}
        template = templates.get(waiting, self._phrases.status_many)
        preview = shorten(head.preview, self._phrases.status_preview_chars)
        text = template.format(seq=head.seq, preview=preview, waiting=waiting)
        return Outbound(self.chat_id, PostKind.STATUS, text, request_id=head.request_id)

    def _post(self, slot: _Slot, early: bool = False) -> None:
        self._emit(
            Outbound(
                self.chat_id,
                slot.kind,
                slot.text or "",
                request_id=slot.request_id,
                seq=slot.seq,
                reply_to=slot.reply_to,
                released_early=early,
                outcome=slot.outcome,
            )
        )
