from __future__ import annotations

from string import Template
from typing import Any, Literal, Self

from anthropic.types.beta import BetaToolParam
from pydantic import BaseModel, PositiveFloat, PositiveInt, model_validator

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Speed = Literal["standard", "fast"]
FAST_MODE_BETA = "fast-mode-2026-02-01"


class ClaudePrompts(BaseModel, frozen=True):
    router: str
    quick: str
    research: str


class ClaudeReplies(BaseModel, frozen=True):
    refusal: str
    out_of_steps: str
    cut_short: str
    addition_not_applied: str


class ToolSpec(BaseModel, frozen=True):
    name: str
    description: str


class ClaudeConfig(BaseModel, frozen=True):
    router_model: str
    quick_model: str
    research_model: str
    router_max_tokens: PositiveInt
    quick_max_tokens: PositiveInt
    research_max_tokens: PositiveInt
    research_effort: Effort
    research_speed: Speed
    research_max_steps: PositiveInt
    tool_timeout_s: PositiveFloat
    betas: tuple[str, ...]
    fallbacks: Literal["default"]
    escalate_sentinel: str
    steer_prefix: str
    max_answer_words: PositiveInt
    history_turns: PositiveInt
    history_answer_chars: PositiveInt
    prompts: ClaudePrompts
    replies: ClaudeReplies
    tools: tuple[ToolSpec, ...]
    tool_input_schema: dict[str, Any]

    @model_validator(mode="after")
    def _prompts_render(self) -> Self:
        for name, prompt in self.prompts:
            try:
                self.render(prompt)
            except KeyError as e:
                raise ValueError(f"prompt {name!r} uses unknown placeholder ${e.args[0]}") from e
        return self

    def research_betas(self) -> list[str]:
        fast = [FAST_MODE_BETA] if self.research_speed == "fast" else []
        return [*self.betas, *fast]

    def render(self, template: str) -> str:
        return Template(template).substitute(
            steer_prefix=self.steer_prefix,
            escalate_sentinel=self.escalate_sentinel,
            max_answer_words=self.max_answer_words,
        )

    def tool_params(self) -> list[BetaToolParam]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": self.tool_input_schema,
                "strict": True,
            }
            for tool in sorted(self.tools, key=lambda t: t.name)
        ]
