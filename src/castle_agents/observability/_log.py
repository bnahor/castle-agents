from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import IO, Any

import orjson

_OPTIONS = orjson.OPT_APPEND_NEWLINE | orjson.OPT_NON_STR_KEYS


class EventLog:
    def __init__(
        self, path: Path | None = None, memory: int | None = None, flush_every: int = 256
    ) -> None:
        self.path = path
        self.events: deque[dict[str, Any]] = deque(maxlen=memory)
        self._t0 = time.monotonic()
        self._flush_every = flush_every
        self._unflushed = 0
        self._file: IO[bytes] | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("ab")

    def now(self) -> float:
        return time.monotonic() - self._t0

    def append(self, chat: str, type: str, **data: Any) -> None:
        event = {"t": round(self.now(), 4), "chat": chat, "type": type, **data}
        self.events.append(event)
        if self._file is None:
            return
        self._file.write(orjson.dumps(event, default=str, option=_OPTIONS))
        self._unflushed += 1
        if self._unflushed >= self._flush_every:
            self.flush()

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._unflushed = 0

    def close(self) -> None:
        if self._file is not None:
            self.flush()
            self._file.close()
            self._file = None
