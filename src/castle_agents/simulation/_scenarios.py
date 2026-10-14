from __future__ import annotations

import random
import tomllib
from collections import Counter
from pathlib import Path
from string import Template
from typing import Any, Self

from pydantic import BaseModel, Field, NonNegativeFloat

from ..domain import Inbound


class Step(BaseModel, frozen=True):
    t: NonNegativeFloat
    chat: str
    user: str
    text: str
    reply_to: str | None = None

    def to_inbound(self, number: int) -> Inbound:
        reply = f"{self.chat}:{self.reply_to}" if self.reply_to else None
        return Inbound(self.chat, f"{self.chat}:m{number}", self.user, self.text, reply)

    def moved(self, chat: str, offset_s: float) -> Self:
        return self.model_copy(update={"chat": chat, "t": self.t + offset_s})


class Scenario(BaseModel, frozen=True):
    name: str
    description: str
    steps: tuple[Step, ...]


class RandomPools(BaseModel, frozen=True):
    max_gap_s: NonNegativeFloat
    edit_rate: float = Field(ge=0, le=1)
    reply_rate: float = Field(ge=0, le=1)
    users: tuple[str, ...]
    tickers: tuple[str, ...]
    questions: tuple[str, ...]
    edits: tuple[str, ...]


def load_scenarios(directory: Path) -> dict[str, Scenario]:
    raw = {
        path.stem: tomllib.loads(path.read_text("utf-8"))
        for path in sorted(directory.glob("*.toml"))
        if path.stem != "random"
    }
    return {name: _build(name, raw) for name in raw}


def load_random_pools(directory: Path) -> RandomPools:
    return RandomPools.model_validate(tomllib.loads((directory / "random.toml").read_text("utf-8")))


def _build(name: str, raw: dict[str, dict[str, Any]]) -> Scenario:
    spec = raw[name]
    if base := spec.get("extends"):
        steps = _fan_out(_build(base, raw).steps, spec["chats"], spec["stagger_s"])
    else:
        steps = tuple(Step(chat=spec["chat"], **message) for message in spec["messages"])
    return Scenario(name=name, description=spec["description"], steps=steps)


def _fan_out(steps: tuple[Step, ...], chats: list[str], stagger_s: float) -> tuple[Step, ...]:
    return tuple(step.moved(chat, i * stagger_s) for i, chat in enumerate(chats) for step in steps)


def random_scenario(pools: RandomPools, seed: int, size: int, chats: int = 2) -> tuple[Step, ...]:
    rng = random.Random(seed)
    steps: list[Step] = []
    sent: Counter[str] = Counter()
    t = 0.0
    for _ in range(size):
        t += rng.uniform(0.0, pools.max_gap_s)
        chat = f"c{rng.randrange(chats)}"
        is_edit = sent[chat] > 0 and rng.random() < pools.edit_rate
        template = rng.choice(pools.edits if is_edit else pools.questions)
        text = Template(template).substitute(
            ticker=rng.choice(pools.tickers), other=rng.choice(pools.tickers)
        )
        replies = sent[chat] > 0 and rng.random() < pools.reply_rate
        reply = f"m{rng.randrange(1, sent[chat] + 1)}" if replies else None
        sent[chat] += 1
        user = rng.choice(pools.users)
        steps.append(Step(t=round(t, 2), chat=chat, user=user, text=text, reply_to=reply))
    return tuple(steps)
