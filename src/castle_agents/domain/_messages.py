from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from ._enums import PostKind


@dataclass(frozen=True, slots=True)
class Inbound:
    chat_id: str
    msg_id: str
    user: str
    text: str
    reply_to: str | None = None


@dataclass(frozen=True, slots=True)
class Outbound:
    chat_id: str
    kind: PostKind
    text: str
    request_id: str | None = None
    seq: int | None = None
    reply_to: str | None = None
    released_early: bool = False

    @property
    def fills_slot(self) -> bool:
        return self.seq is not None


class Exchange(NamedTuple):
    question: str
    answer: str
