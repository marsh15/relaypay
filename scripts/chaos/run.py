"""Chaos harness CLI.

    uv run python -m scripts.chaos.run --mode ci   --seeds 20  --ops 1000   # make chaos
    uv run python -m scripts.chaos.run --mode full --seeds 200 --ops 10000  # make chaos-full
    uv run python -m scripts.chaos.run --mode ci --seeds-list 7 --ops 1000  # repro one seed

Exit status: 0 when the harness found nothing wrong; 1 when a seed hit a harness-level failure
(unexpected exception, no quiescence, provider exactly-once broken) or the invariant checker
reported violations; 2 when ``--require-invariants`` is given and the hand-written checker is not
implemented yet.
"""

import argparse
import json
import multiprocessing
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from scripts.chaos.driver import (
    BANK_POOL_PER_PROCESS,
    CONNECTIONS_PER_SEED,
    SeedResult,
    bank_factory,
    run_seed,
)
from scripts.chaos.ops import generate_schedule, schedule_stats

OUTPUT_DIR = Path("output/chaos")
NOT_EVALUATED = (
    "INVARIANTS NOT EVALUATED: hand-written checker not implemented (relaypay/proof/invariants.py)"
)
DEFAULT_WORKERS = 3  # Compose caps Postgres at one CPU: 2-3 writers saturate it, more thrash
CONNECTION_HEADROOM = 0.6  # share of the free server connections the run may use


_worker_bank: Any = None  # one bank session factory per worker process


def _run_seed_in_worker(seed: int, n_ops: int) -> SeedResult:
    """Entry point of a worker process: seeds run in separate processes because the service
    layer is CPU-heavy Python and one interpreter's GIL would serialise eight threads."""
    global _worker_bank
    if _worker_bank is None:
        manager = bank_factory(BANK_POOL_PER_PROCESS, ensure_account=False)
        _worker_bank = manager.__enter__()
    return run_seed(seed, n_ops, _worker_bank)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.chaos.run", description=__doc__)
    parser.add_argument("--mode", choices=("ci", "full"), default="ci")
    parser.add_argument("--seeds", type=int, default=20, help="run seeds 1..N")
    parser.add_argument(
        "--seeds-list", default=None, help="comma-separated seeds (overrides --seeds)"
    )
    parser.add_argument("--ops", type=int, default=1000, help="operations per seed")
    parser.add_argument("--workers", type=int, default=None, help="parallel seeds (ci mode)")
    parser.add_argument(
        "--require-invariants",
        action="store_true",
        help="exit 2 if the invariant checker is not implemented",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ci: print schedule statistics; full: print the kill/restart plan; touch nothing",
    )
    return parser


def resolve_seeds(args: argparse.Namespace) -> list[int]:
    if args.seeds_list:
        return [int(item) for item in str(args.seeds_list).split(",") if item.strip()]
    return list(range(1, int(args.seeds) + 1))


def plan_workers(requested: int | None, n_seeds: int) -> tuple[int, dict[str, int]]:
    """Choose the number of parallel seeds so every seed's pool fits in the server's connections."""
    from tests.support.db import relaypay_engine

    engine = relaypay_engine("chaos-probe")
    try:
        with engine.connect() as connection:
            max_connections = int(connection.execute(text("SHOW max_connections")).scalar_one())
            in_use = int(
                connection.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar_one()
            )
    finally:
        engine.dispose()
    reserved = 8 + 3  # parent bank pool + superuser reserve
    free = max(0, max_connections - in_use - reserved)
    budget = int(free * CONNECTION_HEADROOM)
    cap = max(1, budget // CONNECTIONS_PER_SEED)
    wanted = requested if requested is not None else min(DEFAULT_WORKERS, n_seeds)
    workers = max(1, min(wanted, n_seeds, cap))
    return workers, {"max_connections": max_connections, "in_use": in_use, "budget": budget}


def _merge(counters: list[dict[str, int]]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for counter in counters:
        total.update(counter)
    return dict(sorted(total.items()))


def run_ci(args: argparse.Namespace) -> int:
    seeds = resolve_seeds(args)
    n_ops = int(args.ops)
    if args.dry_run:
        print(f"chaos[ci] dry run: {len(seeds)} seeds x {n_ops} ops (nothing executed)")
        for seed in seeds[:5]:
            print(f"  seed {seed}: {json.dumps(schedule_stats(generate_schedule(seed, n_ops)))}")
        if len(seeds) > 5:
            print(f"  ... {len(seeds) - 5} more seeds")
        return 0

    workers, connections = plan_workers(args.workers, len(seeds))
    print(
        f"chaos[ci] {len(seeds)} seeds x {n_ops} ops, {workers} parallel "
        f"(server max_connections={connections['max_connections']}, in use "
        f"{connections['in_use']}, run budget {connections['budget']})",
        flush=True,
    )
    started_at = datetime.now(UTC)
    started = time.monotonic()
    results: list[SeedResult] = []
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with bank_factory() as bank:  # creates the chaos bank account before workers connect
        if workers == 1:
            for seed in seeds:
                _collect(run_seed(seed, n_ops, bank), results)
        else:
            context = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
                futures = [pool.submit(_run_seed_in_worker, seed, n_ops) for seed in seeds]
                for future in futures:
                    _collect(future.result(), results)
    elapsed = time.monotonic() - started

    failing = [r for r in results if r.failed]
    evaluated = [r for r in results if r.invariants != "SKIPPED"]
    violations = sum(len(r.violations) for r in results)
    invariants: str | dict[str, Any] = (
        "SKIPPED"
        if not evaluated
        else {
            "violations": sum(
                int(r.invariants["violations"]) for r in evaluated if isinstance(r.invariants, dict)
            ),
            "seeds_evaluated": len(evaluated),
        }
    )
    summary: dict[str, Any] = {
        "mode": "ci",
        "started_at": started_at.isoformat(),
        "seeds": seeds,
        "ops_per_seed": n_ops,
        "workers": workers,
        "ops_by_kind": _merge([r.ops_by_kind for r in results]),
        "crashes_injected": _merge([r.crashes for r in results]),
        "faults_injected": _merge([r.faults for r in results]),
        "rejections": _merge([r.rejections for r in results]),
        "payouts": _merge([r.payouts for r in results]),
        "findings": _merge([r.findings for r in results]),
        "quiesce_passes": {
            "max": max((r.quiesce_passes for r in results), default=0),
            "mean": round(sum(r.quiesce_passes for r in results) / max(len(results), 1), 2),
        },
        "operations_left_in_review": sum(r.review_remaining for r in results),
        "elapsed_seconds": round(elapsed, 2),
        "invariants": invariants,
        "harness_failed_seeds": [r.seed for r in failing if r.failures],
        "violating_seeds": [r.seed for r in results if r.violations],
        "per_seed": [r.summary() for r in results],
    }
    path = OUTPUT_DIR / f"summary-ci-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True))

    print()
    print(
        f"seeds={len(seeds)} ops={sum(r.n_ops for r in results)} elapsed={elapsed:.1f}s "
        f"crashes={sum(sum(r.crashes.values()) for r in results)} "
        f"faults={sum(sum(r.faults.values()) for r in results)} "
        f"max_quiesce_passes={summary['quiesce_passes']['max']}"
    )
    if summary["findings"]:
        print(f"findings (not failures): {json.dumps(summary['findings'])}")
    if invariants == "SKIPPED":
        print(NOT_EVALUATED)
    else:
        print(f"invariants: {violations} violations in {len(evaluated)} seeds")
    harness_failures = [r for r in failing if r.failures]
    print(
        f"harness: {'FAIL' if harness_failures else 'PASS'} "
        f"({len(harness_failures)} seeds with harness failures)"
    )
    print(f"summary: {path}")
    for result in failing:
        print(
            f"repro seed {result.seed}: uv run python -m scripts.chaos.run --mode ci "
            f"--seeds-list {result.seed} --ops {n_ops}"
        )
    if failing:
        return 1
    if invariants == "SKIPPED" and args.require_invariants:
        return 2
    return 0


def _collect(result: SeedResult, results: list[SeedResult]) -> None:
    results.append(result)
    _print_seed(result)
    if result.failed:
        _dump_seed(result)


def _print_seed(result: SeedResult) -> None:
    state = "FAIL" if result.failed else "ok"
    print(
        f"  seed {result.seed:>4}: {result.n_ops} ops in {result.elapsed:6.1f}s "
        f"crashes={sum(result.crashes.values())} faults={sum(result.faults.values())} "
        f"acked={result.acked_ops} passes={result.quiesce_passes} "
        f"review={result.review_remaining} [{state}]",
        flush=True,
    )
    for failure in result.failures[:5]:
        print(f"      ! {failure}")


def _dump_seed(result: SeedResult) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"seed-{result.seed}.json"
    dump = {
        **result.summary(),
        "failures": result.failures,
        "violations": result.violations,
        "schedule": result.schedule,
        "log": result.log,
    }
    path.write_text(json.dumps(dump, indent=2, sort_keys=True))
    print(f"      dumped {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "full":
        from scripts.chaos.full import run_full

        return run_full(args)
    return run_ci(args)


if __name__ == "__main__":
    sys.exit(main())
