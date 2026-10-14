from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, NonNegativeFloat, PositiveInt, model_validator


class MockPatterns(BaseModel, frozen=True):
    cancel: tuple[str, ...]
    rewrite: tuple[str, ...]
    steer: tuple[str, ...]
    heavy: tuple[str, ...]
    needs_data: tuple[str, ...]

    @model_validator(mode="after")
    def _patterns_compile(self) -> Self:
        for words in (self.cancel, self.rewrite, self.steer, self.heavy, self.needs_data):
            anywhere(words)
        return self


class MockReplies(BaseModel, frozen=True):
    quick: str
    research: str
    also_covering: str


class MockConfig(BaseModel, frozen=True):
    router_latency_s: NonNegativeFloat
    quick_latency_s: NonNegativeFloat
    research_base_s: NonNegativeFloat
    research_spread_s: NonNegativeFloat
    research_steps: PositiveInt
    patterns: MockPatterns
    replies: MockReplies

    def scaled(self, scale: float) -> Self:
        return self.model_copy(
            update={
                "router_latency_s": self.router_latency_s * scale,
                "quick_latency_s": self.quick_latency_s * scale,
                "research_base_s": self.research_base_s * scale,
                "research_spread_s": self.research_spread_s * scale,
            }
        )


def leading(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:{'|'.join(words)})", re.IGNORECASE)


def anywhere(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{'|'.join(words)})\b", re.IGNORECASE)
