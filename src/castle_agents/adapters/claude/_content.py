from __future__ import annotations

from typing import Any

from anthropic.types import ContentBlock, TextBlockParam
from anthropic.types.beta import BetaContentBlock

from ...runtime import History
from ._config import ClaudeConfig


def text_of(content: list[ContentBlock] | list[BetaContentBlock]) -> str:
    return "\n".join(block.text for block in content if block.type == "text").strip()


def with_history(text: str, history: History, config: ClaudeConfig) -> str:
    if not history:
        return text
    recent = history[-config.history_turns :]
    lines = ["Recent answers in this chat, for context:"]
    lines += [f"Q: {q}\nA: {a[: config.history_answer_chars]}" for q, a in recent]
    return "\n".join(lines) + f"\n\nQuestion: {text}"


def cached_system(prompt: str) -> list[TextBlockParam]:
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]


def ignore_usage(model: str, usage: Any) -> None:
    pass
