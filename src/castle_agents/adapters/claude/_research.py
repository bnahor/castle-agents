from __future__ import annotations

import asyncio

from anthropic import AsyncAnthropic, omit
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlockParam,
    BetaToolResultBlockParam,
    BetaToolUseBlock,
)

from ...domain import Steering
from ...runtime import History, MarketTools, OnEvent, Unsuccessful, UsageLog
from ._config import ClaudeConfig
from ._content import cached_system, ignore_usage, text_of, with_history


class ClaudeResearcher:
    def __init__(
        self,
        client: AsyncAnthropic,
        tools: MarketTools,
        config: ClaudeConfig,
        log_usage: UsageLog = ignore_usage,
    ) -> None:
        self._client = client
        self._tools = tools
        self._config = config
        self._system = cached_system(config.render(config.prompts.research))
        self._tool_params = config.tool_params()
        self._log_usage = log_usage

    async def research(
        self, text: str, history: History, steering: Steering, on_event: OnEvent
    ) -> str:
        messages: list[BetaMessageParam] = [
            {"role": "user", "content": with_history(text, history, self._config)}
        ]
        last_step = self._config.research_max_steps - 1
        for step in range(self._config.research_max_steps):
            response = await self._sample(messages)
            if response.stop_reason == "refusal":
                raise Unsuccessful(self._config.replies.refusal, "refusal")
            calls_tools = response.stop_reason == "tool_use"
            if not calls_tools and not steering.pending():
                return self._final_text(response)
            if step == last_step:
                raise self._out_of_steps(response, calls_tools)
            messages.append({"role": "assistant", "content": response.content})
            if calls_tools:
                messages.append(await self._tool_turn(response, steering, on_event))
            else:
                messages.append(
                    _user_turn(self._steering_blocks(steering, on_event, "final_check"))
                )
        raise AssertionError("unreachable: the last step always returns or raises")

    def _out_of_steps(self, response: BetaMessage, calls_tools: bool) -> Unsuccessful:
        replies = self._config.replies
        if calls_tools:
            return Unsuccessful(replies.out_of_steps, "out_of_steps")
        text = f"{self._final_text(response)}\n\n{replies.addition_not_applied}"
        return Unsuccessful(text, "addition_not_applied")

    async def _sample(self, messages: list[BetaMessageParam]) -> BetaMessage:
        response = await self._client.beta.messages.create(
            model=self._config.research_model,
            max_tokens=self._config.research_max_tokens,
            output_config={"effort": self._config.research_effort},
            betas=self._config.research_betas(),
            speed="fast" if self._config.research_speed == "fast" else omit,
            fallbacks=self._config.fallbacks,
            cache_control={"type": "ephemeral"},
            system=self._system,
            tools=self._tool_params,
            messages=messages,
        )
        self._log_usage(response.model, response.usage)
        return response

    async def _tool_turn(
        self, response: BetaMessage, steering: Steering, on_event: OnEvent
    ) -> BetaMessageParam:
        calls = [block for block in response.content if block.type == "tool_use"]
        async with asyncio.TaskGroup() as group:
            running = [group.create_task(self._run_tool(call, on_event)) for call in calls]
        blocks: list[BetaContentBlockParam] = [task.result() for task in running]
        return _user_turn(blocks + self._steering_blocks(steering, on_event, "tool_boundary"))

    async def _run_tool(
        self, call: BetaToolUseBlock, on_event: OnEvent
    ) -> BetaToolResultBlockParam:
        on_event("tool", name=call.name, **call.input)
        try:
            async with asyncio.timeout(self._config.tool_timeout_s):
                content = await self._tools.call(call.name, dict(call.input))
            return {"type": "tool_result", "tool_use_id": call.id, "content": content}
        except Exception as e:
            return {
                "type": "tool_result",
                "tool_use_id": call.id,
                "is_error": True,
                "content": f"{type(e).__name__}: {e}",
            }

    def _steering_blocks(
        self, steering: Steering, on_event: OnEvent, at: str
    ) -> list[BetaContentBlockParam]:
        blocks: list[BetaContentBlockParam] = []
        for addition in steering.drain():
            on_event("steer_applied", addition=addition.id, text=addition.text, at=at)
            block: BetaTextBlockParam = {
                "type": "text",
                "text": f"{self._config.steer_prefix} {addition.text}",
            }
            blocks.append(block)
        return blocks

    def _final_text(self, response: BetaMessage) -> str:
        text = text_of(response.content)
        if response.stop_reason == "max_tokens":
            return f"{text}\n\n{self._config.replies.cut_short}"
        return text


def _user_turn(blocks: list[BetaContentBlockParam]) -> BetaMessageParam:
    return {"role": "user", "content": blocks}
