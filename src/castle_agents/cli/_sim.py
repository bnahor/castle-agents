from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..observability import EventLog, Metrics, compute
from ..runtime import Ordering, discard
from ..settings import Settings
from ..simulation import Scenario, load_scenarios, play
from ._report import print_failures, print_table, transcript_printer
from ._wiring import Provider, open_agents


class Mode(StrEnum):
    SERIAL = "serial"
    PARALLEL = "parallel"
    COMPARE = "compare"


@dataclass(frozen=True, slots=True)
class SimRequest:
    scenario: str
    mode: Mode
    provider: Provider
    ordering: Ordering
    scale: float
    quiet: bool
    mock_market_data: bool = False


async def run_sim(settings: Settings, request: SimRequest) -> int:
    scenario = load_scenarios(settings.scenarios_dir)[request.scenario]
    modes = [Mode.SERIAL, Mode.PARALLEL] if request.mode is Mode.COMPARE else [request.mode]
    scale = 1.0 if request.provider is Provider.CLAUDE else request.scale
    run_dir = settings.sim.log_dir / time.strftime("%Y%m%d-%H%M%S")
    print(f"scenario {scenario.name}: {scenario.description}")
    print(f"provider {request.provider}, ordering {request.ordering}, logs in {run_dir}/\n")

    results: dict[str, Metrics] = {}
    for mode in modes:
        show = not request.quiet and (mode is Mode.PARALLEL or len(modes) == 1)
        results[mode] = await _run_mode(settings, request, scenario, mode, scale, run_dir, show)
    print_table(results)
    return 1 if print_failures(results) else 0


async def _run_mode(
    settings: Settings,
    request: SimRequest,
    scenario: Scenario,
    mode: Mode,
    scale: float,
    run_dir: Path,
    show: bool,
) -> Metrics:
    log = EventLog(run_dir / f"{scenario.name}-{mode}.jsonl")
    limits = settings.limits.override(ordering=request.ordering).scaled(scale)
    if mode is Mode.SERIAL:
        limits = limits.as_serial()
    if show:
        print(f"── {mode} transcript " + "─" * 50)
    emit = transcript_printer(scale) if show else discard
    try:
        agents_cm = open_agents(
            request.provider, settings, log, scale, mock_market_data=request.mock_market_data
        )
        async with agents_cm as agents:
            await play(scenario.steps, agents, log, limits, settings.phrases, scale, emit)
    finally:
        log.close()
    if show:
        print()
    return compute(log.events, scale, request.ordering)
