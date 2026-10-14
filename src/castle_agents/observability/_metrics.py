from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..runtime import Ordering

Event = dict[str, Any]

INVARIANTS = ("order_violations", "stale_posts", "duplicate_posts", "missing_posts", "lost_steers")


class Latency(NamedTuple):
    mean_s: float = 0.0
    p50_s: float = 0.0
    p95_s: float = 0.0
    max_s: float = 0.0
    makespan_s: float = 0.0


class Stages(NamedTuple):
    route_s: float = 0.0
    queue_s: float = 0.0
    work_s: float = 0.0
    hold_s: float = 0.0


@dataclass(frozen=True, slots=True)
class Metrics:
    messages: int = 0
    requests: int = 0
    answers: int = 0
    makespan_s: float = 0.0
    latency_mean_s: float = 0.0
    latency_p50_s: float = 0.0
    latency_p95_s: float = 0.0
    latency_max_s: float = 0.0
    stage_route_s: float = 0.0
    stage_queue_s: float = 0.0
    stage_work_s: float = 0.0
    stage_hold_s: float = 0.0
    restarts: int = 0
    steers_applied: int = 0
    escalations: int = 0
    cancels: int = 0
    head_starts_used: int = 0
    early_releases: int = 0
    status_posts: int = 0
    router_calls: int = 0
    order_violations: int = 0
    stale_posts: int = 0
    duplicate_posts: int = 0
    missing_posts: int = 0
    lost_steers: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cpu_share: float = 0.0
    problems: tuple[str, ...] = field(default=())

    @property
    def invariants_ok(self) -> bool:
        return not any(getattr(self, name) for name in INVARIANTS)


def compute(
    events: Iterable[Event], scale: float = 1.0, ordering: Ordering = Ordering.STRICT
) -> Metrics:
    tally = _Tally(scale, ordering)
    for event in events:
        tally.add(event)
    return tally.result()


@dataclass(slots=True)
class _Timeline:
    asked_at: float = 0.0
    admitted_at: float = 0.0
    running_at: float = 0.0
    done_at: float = 0.0
    version: int = 1
    done_versions: set[int] = field(default_factory=set[int])
    accepted: list[str] = field(default_factory=list[str])
    covered: defaultdict[int, set[str]] = field(default_factory=lambda: defaultdict(set))


class _Tally:
    def __init__(self, scale: float, ordering: Ordering) -> None:
        self.scale = scale
        self.ordering = ordering
        self.counts: Counter[str] = Counter()
        self.problems: list[str] = []
        self.inbound_at: dict[str, float] = {}
        self.timelines: defaultdict[str, _Timeline] = defaultdict(_Timeline)
        self.unposted: defaultdict[str, set[int]] = defaultdict(set)
        self.posts: Counter[tuple[str, int]] = Counter()
        self.admitted: set[tuple[str, int]] = set()
        self.cancelled: set[str] = set()
        self.latencies: list[float] = []
        self.stages: list[Stages] = []
        self.first_inbound: float | None = None
        self.last_post: float | None = None
        self.cpu_share = 0.0
        self._handlers: dict[str, Callable[[Event], None]] = {
            "inbound": self._inbound,
            "admit": self._admit,
            "running": self._running,
            "done": self._done,
            "restart": self._restart,
            "escalate": lambda e: self.counts.update(["escalations"]),
            "head_start_used": lambda e: self.counts.update(["head_starts_used"]),
            "routed_batch": lambda e: self.counts.update(["router_calls"]),
            "cancel": self._cancel,
            "addition_accepted": self._addition_accepted,
            "additions_superseded": self._additions_superseded,
            "steer_applied": self._steer_applied,
            "usage": self._usage,
            "outbound": self._outbound,
            "run_stats": self._run_stats,
        }

    def add(self, event: Event) -> None:
        if handler := self._handlers.get(event["type"]):
            handler(event)

    def result(self) -> Metrics:
        for key in sorted(self.admitted - set(self.posts)):
            self._problem("missing_posts", f"never posted {key}")
        counts = {name: self.counts[name] for name in _COUNTED}
        latency, stages = self._latency(), self._stages()
        return Metrics(
            **counts,
            makespan_s=latency.makespan_s,
            latency_mean_s=latency.mean_s,
            latency_p50_s=latency.p50_s,
            latency_p95_s=latency.p95_s,
            latency_max_s=latency.max_s,
            stage_route_s=stages.route_s,
            stage_queue_s=stages.queue_s,
            stage_work_s=stages.work_s,
            stage_hold_s=stages.hold_s,
            cpu_share=self.cpu_share,
            problems=tuple(self.problems),
        )

    def _inbound(self, e: Event) -> None:
        self.counts["messages"] += 1
        self.inbound_at[e["msg_id"]] = e["t"]
        if self.first_inbound is None:
            self.first_inbound = e["t"]

    def _admit(self, e: Event) -> None:
        self.counts["requests"] += 1
        timeline = self.timelines[e["request"]]
        timeline.asked_at = self.inbound_at[e["msg_id"]]
        timeline.admitted_at = e["t"]
        timeline.version = e["version"]
        self.admitted.add((e["chat"], e["seq"]))
        self.unposted[e["chat"]].add(e["seq"])

    def _running(self, e: Event) -> None:
        timeline = self.timelines[e["request"]]
        timeline.running_at = e["t"]
        timeline.covered[e["version"]].update(e.get("included", ()))

    def _done(self, e: Event) -> None:
        timeline = self.timelines[e["request"]]
        timeline.done_at = e["t"]
        timeline.done_versions.add(e["version"])

    def _restart(self, e: Event) -> None:
        self.counts["restarts"] += 1
        self.timelines[e["request"]].version = e["version"]

    def _cancel(self, e: Event) -> None:
        self.counts["cancels"] += 1
        self.cancelled.add(e["request"])
        self.timelines[e["request"]].version = e["version"]

    def _addition_accepted(self, e: Event) -> None:
        self.timelines[e["request"]].accepted.append(e["addition"])

    def _additions_superseded(self, e: Event) -> None:
        accepted = self.timelines[e["request"]].accepted
        accepted[:] = [a for a in accepted if a not in set(e["additions"])]

    def _steer_applied(self, e: Event) -> None:
        self.counts["steers_applied"] += 1
        self.timelines[e["request"]].covered[e["version"]].add(e["addition"])

    def _usage(self, e: Event) -> None:
        self.counts["input_tokens"] += e.get("input") or 0
        self.counts["output_tokens"] += e.get("output") or 0
        self.counts["cache_read_tokens"] += e.get("cache_read") or 0

    def _run_stats(self, e: Event) -> None:
        self.cpu_share = round(e["cpu_s"] / e["wall_s"], 4) if e["wall_s"] else 0.0

    def _outbound(self, e: Event) -> None:
        if e["kind"] == "status":
            self.counts["status_posts"] += 1
        if e.get("seq") is None:
            return
        self._check_slot(e)
        self.last_post = e["t"]
        if e["kind"] == "answer":
            self._record_answer(e)

    def _check_slot(self, e: Event) -> None:
        chat, seq = e["chat"], e["seq"]
        key = (chat, seq)
        self.posts[key] += 1
        if self.posts[key] > 1:
            self._problem("duplicate_posts", f"duplicate post {key}")
        if e.get("early"):
            self.counts["early_releases"] += 1
            if self.ordering is not Ordering.BOUNDED:
                self._problem("order_violations", f"{chat}: #{seq} released early")
        elif self.ordering is not Ordering.REPLY:
            if earlier := [s for s in self.unposted[chat] if s < seq]:
                self._problem("order_violations", f"{chat}: #{seq} posted before #{min(earlier)}")
        self.unposted[chat].discard(seq)

    def _record_answer(self, e: Event) -> None:
        self.counts["answers"] += 1
        request, version = e["request"], e["version"]
        timeline = self.timelines[request]
        current = version == timeline.version and version in timeline.done_versions
        if request in self.cancelled or not current:
            self._problem("stale_posts", f"stale answer for {request} (v{version})")
        for addition in timeline.accepted:
            if addition not in timeline.covered[version]:
                self._problem("lost_steers", f"{request} v{version} lost addition {addition}")
        self.latencies.append((e["t"] - timeline.asked_at) / self.scale)
        self.stages.append(
            Stages(
                route_s=(timeline.admitted_at - timeline.asked_at) / self.scale,
                queue_s=(timeline.running_at - timeline.admitted_at) / self.scale,
                work_s=(timeline.done_at - timeline.running_at) / self.scale,
                hold_s=(e["t"] - timeline.done_at) / self.scale,
            )
        )

    def _latency(self) -> Latency:
        if not self.latencies or self.first_inbound is None or self.last_post is None:
            return Latency()
        ordered = sorted(self.latencies)
        p95 = ordered[0]
        if len(ordered) > 1:
            p95 = statistics.quantiles(ordered, n=20, method="inclusive")[-1]
        return Latency(
            mean_s=round(statistics.mean(ordered), 2),
            p50_s=round(statistics.median(ordered), 2),
            p95_s=round(p95, 2),
            max_s=round(ordered[-1], 2),
            makespan_s=round((self.last_post - self.first_inbound) / self.scale, 2),
        )

    def _stages(self) -> Stages:
        if not self.stages:
            return Stages()
        return Stages(
            *(round(statistics.mean(values), 2) for values in zip(*self.stages, strict=True))
        )

    def _problem(self, counter: str, description: str) -> None:
        self.counts[counter] += 1
        self.problems.append(description)


_COUNTED = (
    "messages",
    "requests",
    "answers",
    "restarts",
    "steers_applied",
    "escalations",
    "cancels",
    "head_starts_used",
    "early_releases",
    "status_posts",
    "router_calls",
    *INVARIANTS,
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
)
