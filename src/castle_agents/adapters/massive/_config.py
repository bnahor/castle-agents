from __future__ import annotations

from pydantic import BaseModel, NonNegativeInt, PositiveFloat


class MassiveConfig(BaseModel, frozen=True):
    connect_timeout_s: PositiveFloat
    read_timeout_s: PositiveFloat
    retries: NonNegativeInt
