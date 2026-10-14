from __future__ import annotations

import functools
import os
import tomllib
from pathlib import Path
from typing import Any

from ._schema import Settings

CONFIG_DIR_ENV = "CASTLE_CONFIG_DIR"


def config_dir() -> Path:
    if override := os.environ.get(CONFIG_DIR_ENV):
        return Path(override)
    here = Path(__file__).resolve()
    candidates = (here.parents[3] / "config", here.parents[1] / "_bundled_config")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"no config directory; set {CONFIG_DIR_ENV}")


@functools.cache
def load_settings(directory: Path | None = None) -> Settings:
    root = directory or config_dir()
    raw = _read_toml(root / "settings.toml")
    return Settings.model_validate(
        {**raw, "claude": _claude(root, raw["claude"]), "directory": root}
    )


def _claude(root: Path, claude: dict[str, Any]) -> dict[str, Any]:
    files = dict(claude["prompts"])
    tools = _read_toml(root / files.pop("tools"))
    prompts = {name: (root / path).read_text("utf-8") for name, path in files.items()}
    return claude | {
        "prompts": prompts,
        "tools": tools["tools"],
        "tool_input_schema": tools["ticker_schema"],
    }


def _read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text("utf-8"))
