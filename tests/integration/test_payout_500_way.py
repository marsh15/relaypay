"""500-way payout race: N concurrent reservations against exactly N/2 payouts of funds.

Parametrised over the legacy production path (control) and the three hand-written strategies.
Exactly ``N // 2`` requests must be RESERVED, the rest INSUFFICIENT, with no other error, no
overdraft and balanced ledger journals. N defaults to 500 when the database allows >= 600
connections (``compose.proof.yaml``), otherwise 80; override with ``PAYOUT_RACE_N``.
"""

import os
import statistics
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
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
from relaypay.payouts.models import Beneficiary, Payout, PayoutReservationHistory
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres, run_in_transaction
from tests.support.fake_provider import FakeProvider
from tests.support.world import balances, build_world, fund_available, make_beneficiary

pytestmark = [pytest.mark.integration, pytest.mark.proof, pytest.mark.concurrency]

AMOUNT = 1_000  # paise per request
FULL_RACE_N = 500
FALLBACK_RACE_N = 80
FULL_RACE_MIN_CONNECTIONS = 600
RESERVE_CONNECTION_HEADROOM = 50  # left for other sessions on a shared database


def _has_balance_version(session: Session) -> bool:
    return bool(
        session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'merchant_accounts' AND column_name = 'balance_version')"
            )
        )
    )


def _has_reservation_guard_trigger(session: Session) -> bool:
    return bool(
        session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE "
                "tgrelid = 'payout_reservation_history'::regclass "
                "AND tgconstraint <> 0 AND tgdeferrable)"
            )
        )
    )


STRATEGIES = [
    pytest.param(LegacyPessimisticReserver, id="legacy_pessimistic"),
    pytest.param(PessimisticReserver, id="pessimistic", marks=pytest.mark.handwritten),
    pytest.param(OptimisticReserver, id="optimistic", marks=pytest.mark.handwritten),
    pytest.param(ConstraintGuardReserver, id="constraint_guard", marks=pytest.mark.handwritten),
]


@dataclass(slots=True)
class Attempt:
    outcome: ReserveOutcome | None = None
    error: BaseException | None = None
    seconds: float = 0.0


@pytest.fixture
def setup_factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("payout-race-setup") as factory:
        yield factory


def _race_size(factory: sessionmaker[Session]) -> tuple[int, int]:
    """Return (N, server max_connections)."""
    max_connections = int(
        run_in_transaction(factory, lambda s: s.scalar(text("SHOW max_connections")))
    )
    configured = os.environ.get("PAYOUT_RACE_N")
    if configured:
        size = int(configured)
    elif max_connections >= FULL_RACE_MIN_CONNECTIONS:
        size = FULL_RACE_N
    else:
        size = FALLBACK_RACE_N
        print(
            f"\n[payout-race] max_connections={max_connections} < {FULL_RACE_MIN_CONNECTIONS}: "
            f"using N={size}; start Postgres with compose.proof.yaml for the full {FULL_RACE_N}."
        )
    if size < 2 or size % 2:
        raise ValueError(f"PAYOUT_RACE_N must be an even number >= 2, got {size}")
    return size, max_connections


def _percentile(sorted_values: list[float], fraction: float) -> float:
    index = min(len(sorted_values) - 1, max(0, round(fraction * len(sorted_values)) - 1))
    return sorted_values[index]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_n_way_payout_race_never_overdraws(
    strategy: type[PayoutReserver], setup_factory: sessionmaker[Session]
) -> None:
    if strategy is OptimisticReserver and not run_in_transaction(
        setup_factory, _has_balance_version
    ):
        pytest.skip("merchant_accounts.balance_version does not exist yet (M19 migration 0026)")
    if strategy is ConstraintGuardReserver and not run_in_transaction(
        setup_factory, _has_reservation_guard_trigger
    ):
        pytest.skip("deferred reservation trigger does not exist yet (M16 migration 0022)")

    race_n, max_connections = _race_size(setup_factory)
    capacity = race_n // 2
    funded = capacity * AMOUNT

    world = build_world(setup_factory, label="payout-race")
    fund_available(setup_factory, world, FakeProvider(), funded)
    public_beneficiary = make_beneficiary(setup_factory, world)
    beneficiary_id = run_in_transaction(
        setup_factory,
        lambda session: session.scalar(
            select(Beneficiary.id).where(Beneficiary.public_id == public_beneficiary)
        ),
    )
    assert beneficiary_id is not None
    before = balances(setup_factory, world)
    assert (before.available, before.reserved, before.payout_eligible) == (funded, 0, funded)

    reserver = strategy()
    attempts = [Attempt() for _ in range(race_n)]
    barrier = threading.Barrier(race_n, timeout=120)
    pool_size = min(race_n, max_connections - RESERVE_CONNECTION_HEADROOM)

    with relaypay_factory(
        f"payout-race-{reserver.name}", pool_size=pool_size, max_overflow=0
    ) as race_factory:

        def worker(slot: int) -> None:
            request = ReserveRequest(
                organisation_id=world.organisation_id,
                environment_id=world.environment_id,
                merchant_account_id=world.merchant_account_id,
                beneficiary_id=beneficiary_id,
                amount=AMOUNT,
                idempotency_key=f"race-{uuid.uuid4().hex}",
                principal=world.admin_principal,
            )
            try:
                barrier.wait()
            except threading.BrokenBarrierError as error:
                attempts[slot].error = error
                return
            started = time.perf_counter()
            try:
                attempts[slot].outcome = reserver.reserve(race_factory, request)
            except BaseException as error:  # every failure must be reported
                attempts[slot].error = error
            attempts[slot].seconds = time.perf_counter() - started

        threads = [threading.Thread(target=worker, args=(slot,)) for slot in range(race_n)]
        wall_start = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)
        elapsed = time.perf_counter() - wall_start
        assert not any(thread.is_alive() for thread in threads), "reserve calls did not finish"

    errors = [(a.error, a.seconds) for a in attempts if a.error is not None]
    outcomes = [a.outcome for a in attempts if a.outcome is not None]
    reserved = [o for o in outcomes if o.status is ReserveStatus.RESERVED]
    insufficient = [o for o in outcomes if o.status is ReserveStatus.INSUFFICIENT]
    replayed = [o for o in outcomes if o.status is ReserveStatus.REPLAYED]
    latencies = sorted(a.seconds for a in attempts if a.outcome is not None)
    p50 = statistics.median(latencies) if latencies else float("nan")
    p95 = _percentile(latencies, 0.95) if latencies else float("nan")
    retries = sum(o.attempts - 1 for o in outcomes)
    print(
        f"\n[payout-race] strategy={reserver.name} N={race_n} max_connections={max_connections} "
        f"pool={pool_size} successes={len(reserved)} conflicts={len(insufficient)} "
        f"replayed={len(replayed)} errors={len(errors)} retries={retries} "
        f"p50={p50 * 1000:.1f}ms p95={p95 * 1000:.1f}ms elapsed={elapsed:.2f}s"
    )

    assert not errors, f"unexpected exceptions: {[repr(e) for e, _ in errors[:5]]}"
    assert not replayed, "every request used a fresh idempotency key"
    assert len(reserved) == capacity
    assert len(insufficient) == race_n - capacity
    assert all(o.payout_id is not None for o in reserved)
    assert all(o.payout_id is None for o in insufficient)
    assert len({o.payout_id for o in reserved}) == capacity

    after = balances(setup_factory, world)
    assert after.reserved == len(reserved) * AMOUNT <= after.available
    assert after.available == funded
    assert after.payout_eligible == 0
    assert min(after.pending, after.available, after.receivable) >= 0
    assert after.reserved >= 0

    def database_view(session: Session) -> tuple[int, int, int, int]:
        payouts = session.scalar(
            select(func.count())
            .select_from(Payout)
            .where(Payout.merchant_account_id == world.merchant_account_id)
        )
        reservation_rows = session.scalar(
            select(func.count())
            .select_from(PayoutReservationHistory)
            .where(PayoutReservationHistory.merchant_account_id == world.merchant_account_id)
        )
        unbalanced = session.scalar(
            text(
                "SELECT count(*) FROM (SELECT j.id FROM journals j "
                "LEFT JOIN postings p ON p.journal_id = j.id "
                "WHERE j.organisation_id = :org GROUP BY j.id "
                "HAVING count(p.id) < 2 OR sum(CASE WHEN p.side = 'DEBIT' THEN p.amount "
                "ELSE -p.amount END) <> 0) bad"
            ),
            {"org": world.organisation_id},
        )
        journals = session.scalar(
            text("SELECT count(*) FROM journals WHERE organisation_id = :org"),
            {"org": world.organisation_id},
        )
        return (
            int(payouts or 0),
            int(reservation_rows or 0),
            int(unbalanced or 0),
            int(journals or 0),
        )

    payouts, reservation_rows, unbalanced, journals = run_in_transaction(
        setup_factory, database_view
    )
    assert payouts == capacity
    assert reservation_rows == capacity
    assert unbalanced == 0
    assert journals >= 1  # the funding capture and settlement are the only journals
