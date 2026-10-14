from __future__ import annotations

import asyncio
import itertools
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator

from ..runtime import Agents, Dispatcher, Emit, EventSink, Limits, Phrases, discard
from ._scenarios import Step


async def play(
    steps: Iterable[Step],
    agents: Agents,
    sink: EventSink,
    limits: Limits,
    phrases: Phrases,
    scale: float = 1.0,
    emit: Emit = discard,
) -> None:
    dispatcher = Dispatcher(agents, sink, limits, phrases, emit)
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    try:
        await _feed(steps, dispatcher, scale)
        await dispatcher.wait_idle()
    finally:
        await dispatcher.close()
        wall_s, cpu_s = time.perf_counter() - wall_start, time.process_time() - cpu_start
        sink.append("-", "run_stats", wall_s=round(wall_s, 4), cpu_s=round(cpu_s, 4))


async def _feed(steps: Iterable[Step], dispatcher: Dispatcher, scale: float) -> None:
    loop = asyncio.get_running_loop()
    start = loop.time()
    numbers: defaultdict[str, Iterator[int]] = defaultdict(lambda: itertools.count(1))
    for step in sorted(steps, key=lambda s: s.t):
        await asyncio.sleep(max(0.0, start + step.t * scale - loop.time()))
        dispatcher.submit(step.to_inbound(next(numbers[step.chat])))
