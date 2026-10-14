from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, PositiveFloat

from ..adapters.claude import ClaudeConfig
from ..adapters.massive import MassiveConfig
from ..adapters.mock import MockConfig
from ..runtime import Limits, Phrases


class SimSettings(BaseModel, frozen=True):
    default_scenario: str
    default_scale: PositiveFloat
    log_dir: Path


class Settings(BaseModel, frozen=True):
    directory: Path
    limits: Limits
    phrases: Phrases
    claude: ClaudeConfig
    massive: MassiveConfig
    mock: MockConfig
    sim: SimSettings

    @property
    def scenarios_dir(self) -> Path:
        return self.directory / "scenarios"

    @property
    def evals_dir(self) -> Path:
        return self.directory / "evals"
