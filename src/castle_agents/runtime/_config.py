from __future__ import annotations

from enum import StrEnum
from string import Formatter
from typing import Any, Self

from pydantic import BaseModel, PositiveFloat, PositiveInt, model_validator


class Ordering(StrEnum):
    STRICT = "strict"
    BOUNDED = "bounded"
    REPLY = "reply"


class Limits(BaseModel, frozen=True):
    heavy_global: PositiveInt
    heavy_per_chat: PositiveInt
    simple_global: PositiveInt
    serial: bool
    ordering: Ordering
    max_hold_s: PositiveFloat
    status_after_s: PositiveFloat
    recent_window: PositiveInt
    route_batch_max: PositiveInt
    speculate_quick: bool
    history_window: PositiveInt
    retain_closed: PositiveInt
    request_deadline_s: PositiveFloat
    router_deadline_s: PositiveFloat
    max_additions: PositiveInt
    max_addition_chars: PositiveInt
    close_grace_s: PositiveFloat

    def override(self, **changes: Any) -> Self:
        return self.model_validate(self.model_dump() | changes)

    def scaled(self, scale: float) -> Self:
        return self.override(
            max_hold_s=self.max_hold_s * scale,
            status_after_s=self.status_after_s * scale,
            request_deadline_s=self.request_deadline_s * scale,
            router_deadline_s=self.router_deadline_s * scale,
            close_grace_s=self.close_grace_s * scale,
        )

    def as_serial(self) -> Self:
        return self.override(
            serial=True, heavy_per_chat=1, route_batch_max=1, speculate_quick=False
        )


_PLACEHOLDERS: dict[str, set[str]] = {
    "ack_steer": {"seq"},
    "too_many_additions": {"seq", "limit"},
    "addition_too_long": {"seq"},
    "ack_rewrite": {"seq", "preview"},
    "ack_cancel": {"seq"},
    "already_closed": {"seq", "status"},
    "status_alone": {"seq", "preview"},
    "status_one": {"seq", "preview"},
    "status_many": {"seq", "preview", "waiting"},
    "apology": {"seq"},
    "timed_out": {"seq"},
    "not_processed": set(),
    "follow_up": {"original", "addition"},
    "fold": {"original", "addition"},
}


class Phrases(BaseModel, frozen=True):
    ack_steer: str
    too_many_additions: str
    addition_too_long: str
    ack_rewrite: str
    ack_cancel: str
    already_closed: str
    status_alone: str
    status_one: str
    status_many: str
    apology: str
    timed_out: str
    not_processed: str
    follow_up: str
    fold: str
    preview_chars: PositiveInt
    status_preview_chars: PositiveInt

    @model_validator(mode="after")
    def _known_placeholders_only(self) -> Self:
        for name, allowed in _PLACEHOLDERS.items():
            used = {field for _, field, _, _ in Formatter().parse(getattr(self, name)) if field}
            if unknown := used - allowed:
                raise ValueError(f"phrases.{name} uses unknown placeholders {sorted(unknown)}")
        return self
