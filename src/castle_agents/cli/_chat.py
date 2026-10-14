from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Iterator

from ..domain import Inbound
from ..observability import EventLog
from ..runtime import Dispatcher, Ordering
from ..settings import Settings
from ._report import transcript_printer
from ._wiring import Provider, open_agents

CHAT_MEMORY_EVENTS = 1_000
USAGE = "Type `user: message`, or `user>m3: message` to reply to message 3. Ctrl-D to quit.\n"


async def run_chat(
    settings: Settings,
    provider: Provider,
    ordering: Ordering,
    chat: str,
    mock_market_data: bool = False,
) -> int:
    path = settings.sim.log_dir / f"chat-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log = EventLog(path, memory=CHAT_MEMORY_EVENTS)
    limits = settings.limits.override(ordering=ordering)
    print(USAGE)
    try:
        agents_cm = open_agents(provider, settings, log, 1.0, mock_market_data=mock_market_data)
        async with agents_cm as agents:
            dispatcher = Dispatcher(agents, log, limits, settings.phrases, transcript_printer(1.0))
            try:
                await _read_lines(chat, dispatcher)
                await dispatcher.wait_idle()
            finally:
                await dispatcher.close()
    finally:
        log.close()
    return 0


async def _read_lines(chat: str, dispatcher: Dispatcher) -> None:
    numbers: Iterator[int] = itertools.count(1)
    while True:
        try:
            line = await asyncio.to_thread(input)
        except EOFError:
            return
        if ":" in line:
            msg = _parse_line(chat, next(numbers), line)
            print(f"  ({msg.msg_id})")
            dispatcher.submit(msg)


def _parse_line(chat: str, number: int, line: str) -> Inbound:
    head, text = line.split(":", 1)
    user, _, reply = head.partition(">")
    reply_to = f"{chat}:{reply.strip()}" if reply.strip() else None
    return Inbound(chat, f"{chat}:m{number}", user.strip() or "me", text.strip(), reply_to)
