from __future__ import annotations

import time

from ..domain import Outbound
from ..observability import Metrics
from ..runtime import Emit

ROWS = (
    ("messages", "messages"),
    ("requests admitted", "requests"),
    ("answers posted", "answers"),
    ("makespan (s)", "makespan_s"),
    ("latency mean (s)", "latency_mean_s"),
    ("latency p50 (s)", "latency_p50_s"),
    ("latency p95 (s)", "latency_p95_s"),
    ("latency max (s)", "latency_max_s"),
    ("  stage: routing (s)", "stage_route_s"),
    ("  stage: queued (s)", "stage_queue_s"),
    ("  stage: working (s)", "stage_work_s"),
    ("  stage: held for order (s)", "stage_hold_s"),
    ("router calls", "router_calls"),
    ("quick head starts used", "head_starts_used"),
    ("released early (bounded)", "early_releases"),
    ("restarts (rewrites)", "restarts"),
    ("steers applied mid-run", "steers_applied"),
    ("escalations", "escalations"),
    ("cancels", "cancels"),
    ("status posts", "status_posts"),
    ("order violations", "order_violations"),
    ("stale posts", "stale_posts"),
    ("duplicate posts", "duplicate_posts"),
    ("missing posts", "missing_posts"),
    ("lost steers", "lost_steers"),
    ("input tokens", "input_tokens"),
    ("output tokens", "output_tokens"),
    ("cache read tokens", "cache_read_tokens"),
    ("cpu share of wall time", "cpu_share"),
)


def transcript_printer(scale: float) -> Emit:
    started = time.perf_counter()

    def emit(out: Outbound) -> None:
        elapsed = (time.perf_counter() - started) / scale
        tag = f"#{out.seq}" if out.fills_slot else out.kind
        threaded = out.reply_to and (out.released_early or not out.fills_slot)
        reply = f" ↩{out.reply_to}" if threaded else ""
        print(f"  [{elapsed:6.2f}s] {out.chat_id:<7} {tag:>7}{reply}  {out.text}")

    return emit


def print_table(results: dict[str, Metrics]) -> None:
    modes = list(results)
    print(f"{'metric':<24}" + "".join(f"{mode:>12}" for mode in modes))
    print("─" * (24 + 12 * len(modes)))
    for label, attr in ROWS:
        values = "".join(f"{getattr(results[mode], attr)!s:>12}" for mode in modes)
        print(f"{label:<24}{values}")
    if "serial" in results and "parallel" in results:
        _print_speedup(results["serial"], results["parallel"])


def _print_speedup(serial: Metrics, parallel: Metrics) -> None:
    if not parallel.latency_mean_s:
        return
    latency = serial.latency_mean_s / parallel.latency_mean_s
    makespan = serial.makespan_s / max(parallel.makespan_s, 0.01)
    print(f"\nparallel cuts mean latency {latency:.1f}x and makespan {makespan:.1f}x.")


def print_failures(results: dict[str, Metrics]) -> bool:
    failed = [mode for mode, metrics in results.items() if not metrics.invariants_ok]
    for mode in failed:
        print(f"\nINVARIANT FAILURES ({mode}):", *results[mode].problems, sep="\n  ")
    return bool(failed)
