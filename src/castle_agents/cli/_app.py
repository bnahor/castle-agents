from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from dotenv import find_dotenv, load_dotenv

from ..runtime import Ordering
from ..settings import Settings, load_settings
from ..simulation import load_scenarios
from ._chat import run_chat
from ._eval import run_router_eval
from ._sim import Mode, SimRequest, run_sim
from ._wiring import MissingCredentials, Provider

MOCK_MARKET_HELP = "with --provider claude: use fake market data instead of Massive"


def main(argv: list[str] | None = None) -> int:
    load_dotenv(find_dotenv(usecwd=True))
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    args = _parser(settings).parse_args(argv)
    try:
        return _dispatch(settings, args)
    except MissingCredentials as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _dispatch(settings: Settings, args: argparse.Namespace) -> int:
    match args.command:
        case "scenarios":
            return _list_scenarios(settings)
        case "eval-router":
            return _run(run_router_eval(settings, Provider(args.provider)))
        case "sim":
            request = SimRequest(
                scenario=args.scenario,
                mode=Mode(args.mode),
                provider=Provider(args.provider),
                ordering=Ordering(args.ordering),
                scale=args.scale,
                quiet=args.quiet,
                mock_market_data=args.mock_market_data,
            )
            return _run(run_sim(settings, request))
        case _:
            provider, ordering = Provider(args.provider), Ordering(args.ordering)
            return _run(run_chat(settings, provider, ordering, args.chat, args.mock_market_data))


def _run(main: Coroutine[Any, Any, int]) -> int:
    with asyncio.Runner(loop_factory=_loop_factory()) as runner:
        return runner.run(main)


def _loop_factory() -> Callable[[], asyncio.AbstractEventLoop] | None:
    try:
        import uvloop
    except ImportError:
        return None
    return uvloop.new_event_loop


def _list_scenarios(settings: Settings) -> int:
    for scenario in load_scenarios(settings.scenarios_dir).values():
        print(f"{scenario.name:<8} {len(scenario.steps):>3} msgs  {scenario.description}")
    return 0


def _parser(settings: Settings) -> argparse.ArgumentParser:
    scenarios = sorted(load_scenarios(settings.scenarios_dir))
    providers = [p.value for p in Provider]
    orderings = [o.value for o in Ordering]
    default_ordering = settings.limits.ordering.value

    parser = argparse.ArgumentParser(prog="castle-agents")
    commands = parser.add_subparsers(dest="command", required=True)

    sim = commands.add_parser("sim", help="replay a scripted busy chat")
    sim.add_argument(
        "scenario", nargs="?", default=settings.sim.default_scenario, choices=scenarios
    )
    sim.add_argument("--mode", default=Mode.COMPARE.value, choices=[m.value for m in Mode])
    sim.add_argument("--provider", default=Provider.MOCK.value, choices=providers)
    sim.add_argument("--ordering", default=default_ordering, choices=orderings)
    sim.add_argument("--scale", type=float, default=settings.sim.default_scale)
    sim.add_argument("--quiet", action="store_true")
    sim.add_argument("--mock-market-data", action="store_true", help=MOCK_MARKET_HELP)

    chat = commands.add_parser("chat", help="interactive group chat")
    chat.add_argument("--provider", default=Provider.MOCK.value, choices=providers)
    chat.add_argument("--ordering", default=default_ordering, choices=orderings)
    chat.add_argument("--chat", default="desk")
    chat.add_argument("--mock-market-data", action="store_true", help=MOCK_MARKET_HELP)

    commands.add_parser("scenarios", help="list scenarios")

    evaluate = commands.add_parser("eval-router", help="score the router on labelled cases")
    evaluate.add_argument("--provider", default=Provider.MOCK.value, choices=providers)
    return parser
