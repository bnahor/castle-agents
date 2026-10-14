from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from ..domain import Action, Inbound, Kind, RequestView, Route, Status
from ..runtime import Router


class _RequestSpec(BaseModel, frozen=True):
    id: str
    seq: int
    user: str
    status: Status
    text: str
    msg_ids: tuple[str, ...]


class _MessageSpec(BaseModel, frozen=True):
    msg_id: str
    user: str
    text: str
    reply_to: str | None = None


class Expected(BaseModel, frozen=True):
    msg_id: str
    action: Action
    target: str | None = None
    complexity: Kind | None = None


class Case(BaseModel, frozen=True):
    name: str
    requests: tuple[_RequestSpec, ...]
    messages: tuple[_MessageSpec, ...]
    expect: tuple[Expected, ...]

    def batch(self) -> list[Inbound]:
        return [Inbound("desk", m.msg_id, m.user, m.text, m.reply_to) for m in self.messages]

    def recent(self) -> list[RequestView]:
        return [
            RequestView(r.id, r.seq, r.user, r.text, r.status, r.msg_ids) for r in self.requests
        ]


@dataclass(frozen=True, slots=True)
class Miss:
    case: str
    msg_id: str
    expected: str
    got: str


@dataclass(frozen=True, slots=True)
class RouterReport:
    decisions: int
    actions_right: int
    targets_checked: int
    targets_right: int
    complexity_checked: int
    complexity_right: int
    misses: tuple[Miss, ...]


def load_cases(path: Path) -> list[Case]:
    raw = tomllib.loads(path.read_text("utf-8"))
    return [Case.model_validate(case) for case in raw["cases"]]


async def evaluate_router(router: Router, cases: Sequence[Case]) -> RouterReport:
    tally = _Tally()
    for case in cases:
        routes = {route.msg_id: route for route in await router.route(case.batch(), case.recent())}
        for expected in case.expect:
            tally.score(case.name, expected, routes.get(expected.msg_id))
    return tally.report()


class _Tally:
    def __init__(self) -> None:
        self.decisions = self.actions = 0
        self.targets_checked = self.targets = 0
        self.complexity_checked = self.complexity = 0
        self.misses: list[Miss] = []

    def score(self, case: str, expected: Expected, got: Route | None) -> None:
        self.decisions += 1
        if got is None:
            self.misses.append(Miss(case, expected.msg_id, _describe(expected), "no route"))
            return
        right = got.action is expected.action
        self.actions += right
        if expected.target is not None:
            self.targets_checked += 1
            hit = got.target == expected.target
            self.targets += hit
            right = right and hit
        if expected.complexity is not None:
            self.complexity_checked += 1
            hit = got.complexity is expected.complexity
            self.complexity += hit
            right = right and hit
        if not right:
            got_text = f"{got.action} {got.complexity} target={got.target}"
            self.misses.append(Miss(case, expected.msg_id, _describe(expected), got_text))

    def report(self) -> RouterReport:
        return RouterReport(
            self.decisions,
            self.actions,
            self.targets_checked,
            self.targets,
            self.complexity_checked,
            self.complexity,
            tuple(self.misses),
        )


def _describe(expected: Expected) -> str:
    parts = [str(expected.action)]
    if expected.complexity:
        parts.append(str(expected.complexity))
    if expected.target:
        parts.append(f"target={expected.target}")
    return " ".join(parts)
