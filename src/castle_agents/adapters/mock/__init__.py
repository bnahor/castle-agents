from ...runtime import Agents
from ._answerers import MockQuickAnswerer, MockResearcher
from ._config import MockConfig, MockPatterns, MockReplies
from ._router import MockRouter
from ._tools import MockTools


def mock_agents(config: MockConfig, scale: float = 1.0) -> Agents:
    scaled = config.scaled(scale)
    return Agents(MockRouter(scaled), MockQuickAnswerer(scaled), MockResearcher(scaled))


__all__ = [
    "MockConfig",
    "MockPatterns",
    "MockQuickAnswerer",
    "MockReplies",
    "MockResearcher",
    "MockRouter",
    "MockTools",
    "mock_agents",
]
