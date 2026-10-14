from __future__ import annotations

import json
from collections.abc import Sequence

from anthropic import AsyncAnthropic

from ...domain import Inbound, RequestView, Route, RouteBatch
from ...runtime import UsageLog
from ._config import ClaudeConfig
from ._content import cached_system, ignore_usage


class ClaudeRouter:
    def __init__(
        self, client: AsyncAnthropic, config: ClaudeConfig, log_usage: UsageLog = ignore_usage
    ) -> None:
        self._client = client
        self._config = config
        self._system = cached_system(config.render(config.prompts.router))
        self._log_usage = log_usage

    async def route(self, batch: Sequence[Inbound], recent: list[RequestView]) -> list[Route]:
        response = await self._client.messages.parse(
            model=self._config.router_model,
            max_tokens=self._config.router_max_tokens,
            thinking={"type": "disabled"},
            system=self._system,
            messages=[{"role": "user", "content": _payload(batch, recent)}],
            output_format=RouteBatch,
        )
        self._log_usage(self._config.router_model, response.usage)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"router produced no decision (stop_reason={response.stop_reason})")
        return response.parsed_output.routes


def _payload(batch: Sequence[Inbound], recent: list[RequestView]) -> str:
    requests = [
        {
            "id": r.id,
            "seq": r.seq,
            "user": r.user,
            "status": r.status,
            "text": r.text,
            "msg_ids": list(r.msg_ids),
        }
        for r in recent
    ]
    new_messages = [
        {"msg_id": m.msg_id, "user": m.user, "text": m.text, "reply_to": m.reply_to} for m in batch
    ]
    return json.dumps({"requests": requests, "new_messages": new_messages})
