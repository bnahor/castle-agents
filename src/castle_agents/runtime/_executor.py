from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import NamedTuple

from ..domain import Kind, Request, Status
from ._config import Limits
from ._ports import Agents, Escalate, History, OnEvent


class Superseded(Exception):
    pass


class Scope(NamedTuple):
    text: str
    additions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Job:
    request: Request
    version: int
    lane: asyncio.Semaphore
    history: History
    note: OnEvent
    scope: Callable[[], Scope]


class Executor:
    def __init__(self, agents: Agents, limits: Limits) -> None:
        self._agents = agents
        self._serial = limits.serial
        self._deadline_s = limits.request_deadline_s
        self._research_pool = asyncio.Semaphore(limits.heavy_global)
        self._quick_pool = asyncio.Semaphore(limits.simple_global)

    def speculate(self, text: str, history: History) -> asyncio.Task[str]:
        task = asyncio.create_task(self._speculative_answer(text, history))
        task.add_done_callback(_consume_exception)
        return task

    async def run(self, job: Job) -> str:
        async with asyncio.timeout(self._deadline_s):
            if job.request.kind is Kind.SIMPLE:
                try:
                    return await self._answer_quickly(job)
                except Escalate:
                    job.request.kind = Kind.HEAVY
                    job.note("escalate")
            return await self._research(job)

    async def _speculative_answer(self, text: str, history: History) -> str:
        async with self._quick_pool:
            return await self._agents.quick.answer(text, history)

    async def _answer_quickly(self, job: Job) -> str:
        if head_start := job.request.head_start:
            _claim(job)
            job.note("head_start_used", ready=head_start.done())
            return await head_start
        async with self._quick_gate(job):
            scope = _claim(job)
            return await self._agents.quick.answer(scope.text, job.history)

    async def _research(self, job: Job) -> str:
        async with job.lane, self._research_pool:
            scope = _claim(job)
            request = job.request
            request.steering.drain()  # everything accepted so far is already in scope.text
            return await self._agents.researcher.research(
                scope.text, job.history, request.steering, job.note
            )

    def _quick_gate(self, job: Job) -> AbstractAsyncContextManager[object]:
        return job.lane if self._serial else self._quick_pool


def _claim(job: Job) -> Scope:
    if job.request.version != job.version:
        raise Superseded()
    scope = job.scope()
    job.request.status = Status.RUNNING
    job.note("running", kind=job.request.kind, included=list(scope.additions))
    return scope


def _consume_exception(task: asyncio.Task[str]) -> None:
    if not task.cancelled():
        task.exception()
