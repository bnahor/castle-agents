from __future__ import annotations

from enum import StrEnum


class Kind(StrEnum):
    SIMPLE = "simple"
    HEAVY = "heavy"


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @property
    def closed(self) -> bool:
        return self in (Status.DELIVERED, Status.CANCELLED)


class Action(StrEnum):
    NEW = "new"
    AMEND_RESTART = "amend_restart"
    AMEND_STEER = "amend_steer"
    CANCEL = "cancel"


class Outcome(StrEnum):
    ANSWERED = "answered"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class PostKind(StrEnum):
    ANSWER = "answer"
    STATUS = "status"
    ACK = "ack"
    SKIP = "skip"
