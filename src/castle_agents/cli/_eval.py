from __future__ import annotations

from ..evaluation import RouterReport, evaluate_router, load_cases
from ..observability import EventLog
from ..settings import Settings
from ._wiring import Provider, open_agents


async def run_router_eval(settings: Settings, provider: Provider) -> int:
    cases = load_cases(settings.evals_dir / "router.toml")
    log = EventLog()
    async with open_agents(provider, settings, log, scale=0.0, mock_market_data=True) as agents:
        report = await evaluate_router(agents.router, cases)
    _print(provider, len(cases), report)
    return 0


def _print(provider: Provider, cases: int, report: RouterReport) -> None:
    print(f"router eval ({provider}): {cases} cases, {report.decisions} decisions")
    print(f"  action      {report.actions_right}/{report.decisions}")
    print(f"  target      {report.targets_right}/{report.targets_checked}")
    print(f"  complexity  {report.complexity_right}/{report.complexity_checked}")
    for miss in report.misses:
        print(f"  MISS {miss.case} [{miss.msg_id}]: expected {miss.expected}, got {miss.got}")
