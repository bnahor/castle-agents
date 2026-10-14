from __future__ import annotations

from collections.abc import Callable

from ..domain import Inbound, Outbound
from ._chat import ChatRuntime
from ._config import Limits, Phrases
from ._executor import Executor
from ._ports import Agents, EventSink

Emit = Callable[[Outbound], None]


def discard(out: Outbound) -> None:
    pass


class Dispatcher:
    def __init__(
        self,
        agents: Agents,
        sink: EventSink,
        limits: Limits,
        phrases: Phrases,
        emit: Emit = discard,
    ) -> None:
        self.agents = agents
        self.sink = sink
        self.limits = limits
        self.phrases = phrases
        self.emit = emit
        self.executor = Executor(agents, limits)
        self._chats: dict[str, ChatRuntime] = {}

    def chat(self, chat_id: str) -> ChatRuntime:
        if chat_id not in self._chats:
            self._chats[chat_id] = ChatRuntime(chat_id, self)
        return self._chats[chat_id]

    def submit(self, msg: Inbound) -> None:
        self.chat(msg.chat_id).submit(msg)

    async def wait_idle(self) -> None:
        while any([await chat.settle() for chat in list(self._chats.values())]):
            pass

    async def close(self) -> None:
        for chat in self._chats.values():
            await chat.close()
