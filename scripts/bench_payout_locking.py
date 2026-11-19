"""Benchmark the payout reservation strategies under an N-way race (M19 T19.3).

For every strategy it funds exactly ``N / 2`` payouts of balance, releases ``N`` concurrent
reservations from a barrier, and records: successes (RESERVED), conflicts (INSUFFICIENT), CAS
retries, errors, per-request p50 and p95 latency, the wall time, deadlocks (the delta of
``pg_stat_database.deadlocks``) and lock waits (sampled from ``pg_stat_activity``: the number of
samples in which at least one backend waited on a lock, and the most backends waiting at once).

Every run must also be correct: exactly ``N / 2`` reserved, the rest insufficient, no errors and
no overdraft. A strategy that breaks that is reported with ``correct: false`` and the script exits
non-zero, so a faster-but-wrong number can never be published.

    docker compose -f compose.yaml -f compose.proof.yaml up -d postgres   # N=500 needs this
    uv run python -m scripts.bench_payout_locking --n 500 --rounds 3 \
        --output docs/evidence/payout-locking-bench.json

The numbers describe one machine and one Postgres, stated in the output. They are a comparison
between strategies, not a throughput target.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relaypay.payouts.locking.constraint_guard import ConstraintGuardReserver
from relaypay.payouts.locking.legacy import LegacyPessimisticReserver
from relaypay.payouts.locking.optimistic import OptimisticReserver
from relaypay.payouts.locking.pessimistic import PessimisticReserver
from relaypay.payouts.locking.types import (
    PayoutReserver,
    ReserveOutcome,
    ReserveRequest,
    ReserveStatus,
)
from relaypay.payouts.models import Beneficiary
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, run_in_transaction
from tests.support.fake_provider import FakeProvider
from tests.support.world import balances, build_world, fund_available, make_beneficiary

AMOUNT = 1_000  # paise per request
HEADROOM = 50  # connections left for the monitor and other sessions
SAMPLE_SECONDS = 0.02

STRATEGIES: tuple[type[PayoutReserver], ...] = (
    LegacyPessimisticReserver,
    PessimisticReserver,
    OptimisticReserver,
    ConstraintGuardReserver,
)


@dataclass(slots=True)
class _Attempt:
    outcome: ReserveOutcome | None = None
    error: str | None = None
    seconds: float = 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))]


def _deadlocks(factory: sessionmaker[Session]) -> int:
    return int(
        run_in_transaction(
            factory,
            lambda s: s.scalar(
                text("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
            ),
        )
        or 0
    )


class _LockWaitSampler(threading.Thread):
    """Counts how often, and how widely, backends wait on a lock while a race runs."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        super().__init__(daemon=True)
        self._factory = factory
        self._stop_event = threading.Event()
        self.samples = 0
        self.waiting_samples = 0
        self.max_waiting = 0

    def run(self) -> None:
        while not self._stop_event.is_set():
            waiting = int(
                run_in_transaction(
                    self._factory,
                    lambda s: s.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                        )
                    ),
                )
                or 0
            )
            self.samples += 1
            if waiting:
                self.waiting_samples += 1
            self.max_waiting = max(self.max_waiting, waiting)
            self._stop_event.wait(SAMPLE_SECONDS)

    def finish(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)


def _one_round(
    setup: sessionmaker[Session],
    monitor: sessionmaker[Session],
    strategy: type[PayoutReserver],
    *,
    race_n: int,
    pool_size: int,
) -> dict[str, Any]:
    capacity = race_n // 2
    funded = capacity * AMOUNT
    world = build_world(setup, label=f"bench-{strategy.name}")
    fund_available(setup, world, FakeProvider(), funded)
    public_beneficiary = make_beneficiary(setup, world)
    beneficiary_id = run_in_transaction(
        setup,
        lambda s: s.scalar(
            select(Beneficiary.id).where(Beneficiary.public_id == public_beneficiary)
        ),
    )
    assert beneficiary_id is not None

    reserver = strategy()
    attempts = [_Attempt() for _ in range(race_n)]
    barrier = threading.Barrier(race_n + 1, timeout=300)
    deadlocks_before = _deadlocks(monitor)
    sampler = _LockWaitSampler(monitor)

    with relaypay_factory(
        f"bench-{reserver.name}", pool_size=pool_size, max_overflow=0
    ) as race_factory:

        def worker(slot: int) -> None:
            request = ReserveRequest(
                organisation_id=world.organisation_id,
                environment_id=world.environment_id,
                merchant_account_id=world.merchant_account_id,
                beneficiary_id=beneficiary_id,
                amount=AMOUNT,
                idempotency_key=f"bench-{uuid.uuid4().hex}",
                principal=world.admin_principal,
            )
            try:
                barrier.wait()
                started = time.perf_counter()
                attempts[slot].outcome = reserver.reserve(race_factory, request)
                attempts[slot].seconds = time.perf_counter() - started
            except BaseException as error:  # every failure must be reported, not swallowed
                attempts[slot].error = f"{type(error).__name__}: {error}"[:200]

        threads = [threading.Thread(target=worker, args=(slot,)) for slot in range(race_n)]
        for thread in threads:
            thread.start()
        sampler.start()
        barrier.wait()  # releases every worker together with this thread
        wall_start = time.perf_counter()
        for thread in threads:
            thread.join(timeout=600)
        wall = time.perf_counter() - wall_start
        sampler.finish()
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError(f"{reserver.name}: reserve calls did not finish")

    outcomes = [a.outcome for a in attempts if a.outcome is not None]
    errors = [a.error for a in attempts if a.error is not None]
    reserved = sum(o.status is ReserveStatus.RESERVED for o in outcomes)
    insufficient = sum(o.status is ReserveStatus.INSUFFICIENT for o in outcomes)
    latencies = [a.seconds for a in attempts if a.outcome is not None]
    after = balances(setup, world)
    correct = (
        reserved == capacity
        and insufficient == race_n - capacity
        and not errors
        and after.reserved == funded
        and after.payout_eligible == 0
        and after.available >= 0
    )
    return {
        "successes": reserved,
        "conflicts": insufficient,
        "errors": len(errors),
        "errorSamples": sorted(set(errors))[:3],
        "casRetries": sum(o.attempts - 1 for o in outcomes),
        "p50Ms": round(statistics.median(latencies) * 1000, 2) if latencies else None,
        "p95Ms": round(_percentile(latencies, 0.95) * 1000, 2),
        "wallSeconds": round(wall, 3),
        "deadlocks": _deadlocks(monitor) - deadlocks_before,
        "lockWaitSamples": sampler.waiting_samples,
        "lockWaitSamplesTotal": sampler.samples,
        "maxBackendsWaitingOnLock": sampler.max_waiting,
        "correct": correct,
    }


def _combine(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    def total(name: str) -> int:
        return sum(int(r[name]) for r in rounds)

    p50s = [r["p50Ms"] for r in rounds if r["p50Ms"] is not None]
    return {
        "rounds": len(rounds),
        "successes": total("successes"),
        "conflicts": total("conflicts"),
        "errors": total("errors"),
        "casRetries": total("casRetries"),
        "p50Ms": round(statistics.median(p50s), 2) if p50s else None,
        "p95Ms": round(max(r["p95Ms"] for r in rounds), 2),
        "wallSecondsMedian": round(statistics.median(r["wallSeconds"] for r in rounds), 3),
        "deadlocks": total("deadlocks"),
        "lockWaitSamples": total("lockWaitSamples"),
        "maxBackendsWaitingOnLock": max(r["maxBackendsWaitingOnLock"] for r in rounds),
        "correct": all(r["correct"] for r in rounds),
        "perRound": rounds,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", type=int, default=500, help="concurrent reservations (even)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.n < 2 or args.n % 2:
        parser.error("--n must be an even number >= 2")

    with relaypay_factory("bench-setup") as setup, relaypay_factory("bench-monitor") as monitor:
        max_connections = int(
            run_in_transaction(setup, lambda s: s.scalar(text("SHOW max_connections")))
        )
        pool_size = min(args.n, max_connections - HEADROOM)
        if pool_size < args.n:
            print(
                f"warning: max_connections={max_connections} caps the pool at {pool_size}, "
                f"below N={args.n}; the race is narrower than requested (use compose.proof.yaml)",
                file=sys.stderr,
            )
        server_version = run_in_transaction(setup, lambda s: s.scalar(text("SHOW server_version")))
        results: dict[str, Any] = {}
        for strategy in STRATEGIES:
            rounds = []
            for round_number in range(1, args.rounds + 1):
                result = _one_round(setup, monitor, strategy, race_n=args.n, pool_size=pool_size)
                rounds.append(result)
                print(
                    f"{strategy.name:>18} round {round_number}: ok={result['correct']} "
                    f"reserved={result['successes']} conflicts={result['conflicts']} "
                    f"retries={result['casRetries']} p50={result['p50Ms']}ms "
                    f"p95={result['p95Ms']}ms wall={result['wallSeconds']}s "
                    f"deadlocks={result['deadlocks']} "
                    f"maxWaiting={result['maxBackendsWaitingOnLock']}"
                )
            results[strategy.name] = _combine(rounds)

    document = {
        "benchmark": "payout-locking-n-way-race",
        "requestsPerRace": args.n,
        "connectionPool": pool_size,
        "roundsPerStrategy": args.rounds,
        "capacity": args.n // 2,
        "amountPaise": AMOUNT,
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor() or platform.machine(),
            "postgres": str(server_version),
            "postgresMaxConnections": max_connections,
        },
        "strategies": results,
        "allCorrect": all(r["correct"] for r in results.values()),
        "note": "Single machine, one Postgres, threads in one Python process: a comparison "
        "between strategies, not a throughput target.",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.output}")
    return 0 if document["allCorrect"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
