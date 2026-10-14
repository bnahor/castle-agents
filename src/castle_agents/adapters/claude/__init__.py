from anthropic import AsyncAnthropic

from ...runtime import Agents, MarketTools, UsageLog
from ._config import ClaudeConfig, ClaudePrompts, ClaudeReplies, ToolSpec
from ._quick import ClaudeQuickAnswerer
from ._research import ClaudeResearcher
from ._router import ClaudeRouter


def claude_agents(
    client: AsyncAnthropic, tools: MarketTools, config: ClaudeConfig, log_usage: UsageLog
) -> Agents:
    return Agents(
        ClaudeRouter(client, config, log_usage),
        ClaudeQuickAnswerer(client, config, log_usage),
        ClaudeResearcher(client, tools, config, log_usage),
    )


__all__ = [
    "ClaudeConfig",
    "ClaudePrompts",
    "ClaudeQuickAnswerer",
    "ClaudeReplies",
    "ClaudeResearcher",
    "ClaudeRouter",
    "ToolSpec",
    "claude_agents",
]
