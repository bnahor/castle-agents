from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any

from ..adapters.mock import MockTools, mock_agents
from ..domain import Inbound
from ..runtime import Agents, EventSink, MarketTools
from ..settings import Settings

log = logging.getLogger(__name__)

WORKSPACE_ENV = "ANTHROPIC_WORKSPACE_ID"
MASSIVE_KEY_ENV = "MASSIVE_API_KEY"


class Provider(StrEnum):
    MOCK = "mock"
    CLAUDE = "claude"


class MissingCredentials(RuntimeError):
    pass


@asynccontextmanager
async def open_agents(
    provider: Provider,
    settings: Settings,
    sink: EventSink,
    scale: float,
    mock_market_data: bool = False,
) -> AsyncGenerator[Agents, None]:
    match provider:
        case Provider.MOCK:
            yield mock_agents(settings.mock, scale)
        case Provider.CLAUDE:
            tools = _market_tools(settings, mock_market_data)
            async with _claude_agents(settings, sink, tools) as agents:
                yield agents


@asynccontextmanager
async def _claude_agents(
    settings: Settings, sink: EventSink, tools: MarketTools
) -> AsyncGenerator[Agents, None]:
    from anthropic import AsyncAnthropic, DefaultAioHttpClient

    from ..adapters.claude import claude_agents

    workspace = os.environ.get(WORKSPACE_ENV)
    headers = {"anthropic-workspace-id": workspace} if workspace else None

    def log_usage(model: str, usage: Any) -> None:
        sink.append(
            "-",
            "usage",
            model=model,
            input=usage.input_tokens,
            output=usage.output_tokens,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    client = AsyncAnthropic(http_client=DefaultAioHttpClient(), default_headers=headers)
    async with client:
        agents = claude_agents(client, tools, settings.claude, log_usage)
        await _warm_up(agents)
        yield agents


async def _warm_up(agents: Agents) -> None:
    # The first structured-output call compiles the routing schema; pay that before users arrive.
    await agents.router.route([Inbound("warm-up", "warm-up", "system", "hello")], [])


def _market_tools(settings: Settings, mock_market_data: bool) -> MarketTools:
    if mock_market_data:
        log.warning("live models with MOCK market data: figures in answers are fake")
        return MockTools()
    if key := os.environ.get(MASSIVE_KEY_ENV):
        from ..adapters.massive import MassiveTools

        return MassiveTools(key, settings.massive)
    raise MissingCredentials(
        f"{MASSIVE_KEY_ENV} is not set; pass --mock-market-data to use fake data"
    )
