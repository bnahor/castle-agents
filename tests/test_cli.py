from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from castle_agents.settings import config_dir


def _fast_config(tmp_path: Path) -> Path:
    root = tmp_path / "config"
    shutil.copytree(config_dir(), root)
    settings_file = root / "settings.toml"
    text = settings_file.read_text("utf-8")
    for key in ("router_latency_s", "quick_latency_s", "research_base_s", "research_spread_s"):
        line = next(line for line in text.splitlines() if line.startswith(key))
        text = text.replace(line, f"{key} = 0.0")
    text = text.replace('log_dir = "runs"', f'log_dir = "{tmp_path / "runs"}"')
    settings_file.write_text(text, "utf-8")
    return root


def _cli(root: Path, *args: str, stdin: str = "") -> str:
    result = subprocess.run(
        [sys.executable, "-m", "castle_agents", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ | {"CASTLE_CONFIG_DIR": str(root)},
        check=True,
    )
    return result.stdout


def test_the_chat_cli_reads_user_reply_and_text_from_each_line(tmp_path: Path):
    out = _cli(
        _fast_config(tmp_path), "chat", stdin="no colon here\nbob>m7: what does ETF stand for?\n"
    )
    assert "(desk:m1)" in out
    assert "#1  Quick answer: what does ETF stand for?" in out
    assert "(desk:m2)" not in out


def test_a_compressed_sim_still_releases_held_answers_in_bounded_mode(tmp_path: Path):
    root = tmp_path / "config"
    shutil.copytree(config_dir(), root)
    out = _cli(root, "sim", "busy", "--mode", "parallel", "--scale", "0.05", "--quiet")
    released = next(line for line in out.splitlines() if line.startswith("released early"))
    assert int(released.split()[-1]) > 0


def test_a_live_run_without_a_market_data_key_refuses_instead_of_faking_data(tmp_path: Path):
    env = {k: v for k, v in os.environ.items() if k != "MASSIVE_API_KEY"}
    result = subprocess.run(
        [sys.executable, "-m", "castle_agents", "sim", "busy", "--provider", "claude", "--quiet"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env=env | {"CASTLE_CONFIG_DIR": str(config_dir())},
    )
    assert result.returncode == 2
    assert "MASSIVE_API_KEY is not set" in result.stderr
