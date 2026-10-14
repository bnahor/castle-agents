from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from castle_agents.settings import Settings, config_dir, load_settings
from castle_agents.simulation import load_scenarios


def _copy_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(config_dir(), target)
    return target


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text("utf-8")
    assert old in text
    path.write_text(text.replace(old, new), "utf-8")


def test_shipped_config_loads_and_prompts_render(settings: Settings):
    claude = settings.claude
    assert claude.steer_prefix in claude.render(claude.prompts.research)
    assert claude.escalate_sentinel in claude.render(claude.prompts.quick)


def test_tool_definitions_are_sorted_for_a_stable_prompt_prefix(settings: Settings):
    names = [tool["name"] for tool in settings.claude.tool_params()]
    assert names == sorted(names)


def test_multi_scenario_fans_busy_out_across_chats(settings: Settings):
    scenarios = load_scenarios(settings.scenarios_dir)
    busy, multi = scenarios["busy"], scenarios["multi"]
    assert len(multi.steps) == 3 * len(busy.steps)
    assert {step.chat for step in multi.steps} == {"desk-a", "desk-b", "desk-c"}


def test_a_phrase_with_an_unknown_placeholder_is_rejected_at_load(tmp_path: Path):
    root = _copy_config(tmp_path)
    _replace(
        root / "settings.toml", 'ack_cancel = "Okay, dropping #{seq}."', 'ack_cancel = "{oops}"'
    )
    with pytest.raises(ValidationError, match="unknown placeholders"):
        load_settings(root)


def test_a_prompt_with_an_unknown_placeholder_is_rejected_at_load(tmp_path: Path):
    root = _copy_config(tmp_path)
    (root / "prompts" / "quick.md").write_text("reply with $nonexistent", "utf-8")
    with pytest.raises(ValidationError, match=r"unknown placeholder \$nonexistent"):
        load_settings(root)


def test_every_router_eval_target_exists_in_its_case(settings: Settings):
    from castle_agents.evaluation import load_cases

    for case in load_cases(settings.evals_dir / "router.toml"):
        known = {r.id for r in case.requests} | {m.msg_id for m in case.messages}
        for expected in case.expect:
            assert expected.msg_id in {m.msg_id for m in case.messages}, case.name
            assert expected.target is None or expected.target in known, case.name
