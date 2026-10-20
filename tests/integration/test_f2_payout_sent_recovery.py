"""F2: a payout attempt left SENT by a dead worker is recovered by a lookup-only sweep.

Reproduction: ``dispatch_payout_attempt`` under ``crash_plan({"payout.after_bank_response": 1.0})``.
The bank has paid (or declined), the worker died before recording the answer, so the payout is
PROCESSING with attempt SENT and its funds stay reserved. ``run_payout_batch`` used to select only
PREPARED and AMBIGUOUS attempts, so nothing ever looked at it again.

The fix is a lookup-only recovery after a grace period, behind a lease. These tests assert:

* convergence: payout SUCCEEDED or FAILED per the bank's truth, reservation consumed or released
  exactly once, one journal and one event on success;
* exactly one external effect: the bank transfer is never re-sent (``mutations == 1``,
  one ``bank_effects`` row);
* race safety against a live dispatcher that is slow (bank applied before the response is
  recorded, or bank not yet applied) and against two sweeps running at once.
"""

import json
import random
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from relaypay.merchant_balances.service import MerchantBalances
from relaypay.mock_bank.models import BankEffect
from relaypay.mock_bank.service import configure_fault
from relaypay.payouts.models import (
    Payout,
    PayoutAttempt,
    PayoutAttemptEvidence,
    PayoutEvent,
    PayoutReservationHistory,
)
from relaypay.payouts.service import dispatch_payout_attempt, run_payout_batch
from relaypay.proof.crashpoints import SimulatedCrash, crash_plan
from relaypay.provider_operations.service_types import ProviderObservation
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from scripts.chaos.driver import BANK_ACCOUNT, BANK_SECRET, LocalBankTransport, bank_factory
from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import (
    World,
    balances,
    build_world,
    fund_available,
    make_beneficiary,
    request_payout,
)

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]

AMOUNT = 60_000
FUNDED = 100_000


class CountingBank(LocalBankTransport):
    """Local bank that counts mutations and can pause either side of the bank call."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        super().__init__(factory)
        self.mutations = 0
        self.lookups = 0
        self._lock = threading.Lock()
        self.pause_before_apply: threading.Event | None = None
        self.pause_after_apply: threading.Event | None = None
        self.entered = threading.Event()
        self.lookup_gate: threading.Event | None = None
        self.lookup_entered = threading.Event()

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        with self._lock:
            self.mutations += 1
        self.entered.set()
        if self.pause_before_apply is not None:
            assert self.pause_before_apply.wait(10), "test never released the bank"
        observation = super().mutate(request_bytes)
        if self.pause_after_apply is not None:
            assert self.pause_after_apply.wait(10), "test never released the bank"
        return observation

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        with self._lock:
            self.lookups += 1
        self.lookup_entered.set()
        if self.lookup_gate is not None:
            assert self.lookup_gate.wait(10), "test never released the lookup"
        return super().lookup(account_id=account_id, stable_key=stable_key)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("f2-payout-sent-recovery-tests", pool_size=12) as session_factory:
        yield session_factory


@pytest.fixture
def bank() -> Iterator[sessionmaker[Session]]:
    with bank_factory(pool_size=8) as session_factory:
        yield session_factory


def _payout(
    factory: sessionmaker[Session], bank: sessionmaker[Session], *, fault: str | None = None
) -> tuple[World, str, str]:
    world = build_world(factory, label="f2-sent-recovery")
    fund_available(factory, world, FakeProvider(), FUNDED)
    beneficiary = make_beneficiary(factory, world)
    payout_id = str(json.loads(request_payout(factory, world, beneficiary, AMOUNT).body)["id"])
    stable_key = f"payout:{payout_id}:attempt:1"
    if fault is not None:
        configure_fault(
            bank, account_public_id=BANK_ACCOUNT, stable_key=stable_key, fault_type=fault
        )
    return world, payout_id, stable_key


def _worker_dies_after_bank_response(
    factory: sessionmaker[Session], payout_id: str, transport: CountingBank
) -> None:
    with (
        crash_plan({"payout.after_bank_response": 1.0}, random.Random(0)) as crashes,  # noqa: S311
        pytest.raises(SimulatedCrash),
    ):
        dispatch_payout_attempt(
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=transport,
        )
    assert crashes.fired == ("payout.after_bank_response",)


def _age_send(factory: sessionmaker[Session], payout_id: str, *, seconds: int = 120) -> None:
    """Make the SENT attempt look ``seconds`` old (send time and lease clock)."""
    with factory() as session, session.begin():
        attempt_ids = (
            select(PayoutAttempt.id)
            .join(Payout, Payout.id == PayoutAttempt.payout_id)
            .where(Payout.public_id == payout_id)
        )
        session.execute(
            update(PayoutAttempt)
            .where(PayoutAttempt.id.in_(attempt_ids))
            .values(
                last_sent_at=func.now() - timedelta(seconds=seconds),
                updated_at=func.now() - timedelta(seconds=seconds),
            )
        )


def _sweep(
    factory: sessionmaker[Session], world: World, transport: CountingBank, **kwargs: int
) -> int:
    return run_payout_batch(
        factory,
        bank_account_id=BANK_ACCOUNT,
        bank_signing_secret=BANK_SECRET,
        transport=transport,
        organisation_id=world.organisation_id,
        **kwargs,
    )


def _states(factory: sessionmaker[Session], payout_id: str) -> tuple[str, str]:
    with factory() as session, session.begin():
        row = session.execute(
            select(Payout.status, PayoutAttempt.status)
            .join(PayoutAttempt, PayoutAttempt.payout_id == Payout.id)
            .where(Payout.public_id == payout_id)
        ).one()
    return str(row[0]), str(row[1])


def _ledger_effects(factory: sessionmaker[Session], payout_id: str) -> dict[str, int]:
    with factory() as session, session.begin():
        payout = session.scalar(select(Payout).where(Payout.public_id == payout_id))
        assert payout is not None
        reservations = dict(
            session.execute(
                select(PayoutReservationHistory.action, func.count())
                .where(PayoutReservationHistory.payout_id == payout.id)
                .group_by(PayoutReservationHistory.action)
            ).all()
        )
        events = session.scalar(
            select(func.count()).select_from(PayoutEvent).where(PayoutEvent.payout_id == payout.id)
        )
        mutation_sends = session.scalar(
            select(func.count())
            .select_from(PayoutAttemptEvidence)
            .join(PayoutAttempt, PayoutAttempt.id == PayoutAttemptEvidence.payout_attempt_id)
            .where(
                PayoutAttempt.payout_id == payout.id,
                PayoutAttemptEvidence.evidence_kind == "MUTATION_SEND",
            )
        )
        return {
            "reserved": int(reservations.get("RESERVED", 0)),
            "released": int(reservations.get("RELEASED", 0)),
            "consumed": int(reservations.get("CONSUMED", 0)),
            "events": int(events or 0),
            "journaled": int(payout.journal_id is not None),
            "mutation_sends": int(mutation_sends or 0),
        }


def _bank_effects(bank: sessionmaker[Session], stable_key: str) -> list[str]:
    with bank() as session, session.begin():
        return list(
            session.scalars(select(BankEffect.outcome).where(BankEffect.stable_key == stable_key))
        )


def _assert_paid(
    factory: sessionmaker[Session],
    bank: sessionmaker[Session],
    world: World,
    payout_id: str,
    stable_key: str,
    transport: CountingBank,
) -> None:
    assert _states(factory, payout_id) == ("SUCCEEDED", "SUCCEEDED")
    assert _ledger_effects(factory, payout_id) == {
        "reserved": 1,
        "released": 0,
        "consumed": 1,
        "events": 1,
        "journaled": 1,
        "mutation_sends": 1,
    }
    after: MerchantBalances = balances(factory, world)
    assert (after.reserved, after.available) == (0, FUNDED - AMOUNT)
    assert transport.mutations == 1, "the bank transfer was sent more than once"
    assert _bank_effects(bank, stable_key) == ["SUCCEEDED"]


def test_sent_payout_is_recovered_by_lookup_after_the_grace_period(
    factory: sessionmaker[Session], bank: sessionmaker[Session]
) -> None:
    world, payout_id, stable_key = _payout(factory, bank)
    transport = CountingBank(bank)

    _worker_dies_after_bank_response(factory, payout_id, transport)

    # The stuck state the chaos harness found: bank paid, nothing recorded, funds reserved.
    assert _states(factory, payout_id) == ("PROCESSING", "SENT")
    assert balances(factory, world).reserved == AMOUNT
    assert _bank_effects(bank, stable_key) == ["SUCCEEDED"]
    assert transport.mutations == 1

    # Inside the grace period the attempt may still belong to a live dispatcher: leave it alone.
    _sweep(factory, world, transport)
    assert _states(factory, payout_id) == ("PROCESSING", "SENT")
    assert transport.lookups == 0

    _age_send(factory, payout_id)
    _sweep(factory, world, transport)

    _assert_paid(factory, bank, world, payout_id, stable_key, transport)
    assert transport.lookups == 1

    # A later sweep finds nothing to do and changes nothing.
    _sweep(factory, world, transport)
    _assert_paid(factory, bank, world, payout_id, stable_key, transport)
    assert transport.lookups == 1


def test_sent_payout_the_bank_declined_releases_the_reservation(
    factory: sessionmaker[Session], bank: sessionmaker[Session]
) -> None:
    world, payout_id, stable_key = _payout(factory, bank, fault="DECLINE")
    transport = CountingBank(bank)

    _worker_dies_after_bank_response(factory, payout_id, transport)
    assert _states(factory, payout_id) == ("PROCESSING", "SENT")
    _age_send(factory, payout_id)
    _sweep(factory, world, transport)

    assert _states(factory, payout_id) == ("FAILED", "FAILED")
    assert _ledger_effects(factory, payout_id) == {
        "reserved": 1,
        "released": 1,
        "consumed": 0,
        "events": 0,
        "journaled": 0,
        "mutation_sends": 1,
    }
    after = balances(factory, world)
    assert (after.reserved, after.available) == (0, FUNDED)
    assert transport.mutations == 1
    assert _bank_effects(bank, stable_key) == ["DECLINED"]


def test_two_concurrent_sweeps_look_up_a_sent_payout_once(
    factory: sessionmaker[Session], bank: sessionmaker[Session]
) -> None:
    """The lease: while one sweep holds the attempt, a second sweep skips it."""
    world, payout_id, stable_key = _payout(factory, bank)
    transport = CountingBank(bank)
    _worker_dies_after_bank_response(factory, payout_id, transport)
    _age_send(factory, payout_id)

    transport.lookup_gate = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_sweep, factory, world, transport)
        assert transport.lookup_entered.wait(10), "first sweep never reached the bank lookup"
        second_claimed = _sweep(factory, world, transport)  # runs while the first holds the lease
        transport.lookup_gate.set()
        first.result(timeout=20)

    assert second_claimed == 0, "a second sweep claimed an attempt another sweep is recovering"
    assert transport.lookups == 1
    _assert_paid(factory, bank, world, payout_id, stable_key, transport)


def test_recovery_racing_a_live_dispatcher_whose_bank_call_already_applied(
    factory: sessionmaker[Session], bank: sessionmaker[Session]
) -> None:
    """Live dispatcher: bank applied, response not yet recorded. Recovery wins the record."""
    world, payout_id, stable_key = _payout(factory, bank)
    transport = CountingBank(bank)
    transport.pause_after_apply = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        live = pool.submit(
            dispatch_payout_attempt,
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=transport,
        )
        assert transport.entered.wait(10)
        deadline = time.monotonic() + 10
        while _bank_effects(bank, stable_key) != ["SUCCEEDED"]:
            assert time.monotonic() < deadline, "bank never applied the transfer"
            time.sleep(0.01)
        assert _states(factory, payout_id) == ("PROCESSING", "SENT")

        _age_send(factory, payout_id)  # as if the dispatcher had been slow past the grace period
        _sweep(factory, world, transport)
        assert _states(factory, payout_id) == ("SUCCEEDED", "SUCCEEDED")

        transport.pause_after_apply.set()  # the slow dispatcher now records its (stale) response
        live.result(timeout=20)

    _assert_paid(factory, bank, world, payout_id, stable_key, transport)


def test_recovery_racing_a_live_dispatcher_whose_bank_call_has_not_applied_yet(
    factory: sessionmaker[Session], bank: sessionmaker[Session]
) -> None:
    """Live dispatcher: send committed, bank not reached yet. Recovery's lookup finds nothing.

    That lookup is only ambiguous evidence (the payout goes to review); the live dispatcher's
    result then resolves it. Nothing is sent twice and the reservation is consumed once.
    """
    world, payout_id, stable_key = _payout(factory, bank)
    transport = CountingBank(bank)
    transport.pause_before_apply = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        live = pool.submit(
            dispatch_payout_attempt,
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=transport,
        )
        assert transport.entered.wait(10)
        _age_send(factory, payout_id)
        _sweep(factory, world, transport)
        assert transport.lookups == 1
        assert _bank_effects(bank, stable_key) == []

        transport.pause_before_apply.set()
        live.result(timeout=20)

    _assert_paid(factory, bank, world, payout_id, stable_key, transport)
