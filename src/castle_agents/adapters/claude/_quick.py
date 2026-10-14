from __future__ import annotations

from anthropic import AsyncAnthropic

from ...runtime import Escalate, History, UsageLog
from ._config import ClaudeConfig
from ._content import cached_system, ignore_usage, text_of, with_history


class ClaudeQuickAnswerer:
    def __init__(
        self, client: AsyncAnthropic, config: ClaudeConfig, log_usage: UsageLog = ignore_usage
    ) -> None:
        self._client = client
        self._config = config
        self._system = cached_system(config.render(config.prompts.quick))
        self._log_usage = log_usage

    async def answer(self, text: str, history: History) -> str:
        response = await self._client.messages.create(
            model=self._config.quick_model,
            max_tokens=self._config.quick_max_tokens,
            thinking={"type": "disabled"},
            system=self._system,
            messages=[{"role": "user", "content": with_history(text, history, self._config)}],
        )
        self._log_usage(self._config.quick_model, response.usage)
        answer = text_of(response.content)
        if response.stop_reason == "refusal" or answer.startswith(self._config.escalate_sentinel):
            raise Escalate()
        return answer
