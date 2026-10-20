"""F1 directed probe: can a refund consume money already reserved for an in-flight payout?

Scenario: capture -> settle (value moves to AVAILABLE) -> reserve a payout for all of AVAILABLE
-> refund the capture in full -> finalize the payout through the bank path.

``ledger.service._refund_sources`` sizes the AVAILABLE draw from raw AVAILABLE_PAYABLE postings,
while ``merchant_balances.service.derive_balances`` subtracts payout reservations to get
``payout_eligible``. The correct behaviour asserted here: AVAILABLE never goes negative,
reserved never exceeds AVAILABLE, and the refund is funded from the merchant receivable rather
than from the reserved funds. See ``docs/plans/v2.0-never-loses-a-paisa.md`` (M15, F1).
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from relaypay.database import build_engine
from relaypay.merchant_balances.models import BalanceTransaction
from relaypay.merchant_balances.service import MerchantBalances
from relaypay.mock_bank.models import BankAccount
from relaypay.mock_bank.service import apply_transfer, lookup_transfer, parse_command
from relaypay.payouts.models import Payout
from relaypay.payouts.service import dispatch_payout_attempt
from relaypay.provider_operations.service_types import ProviderObservation
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import (
    World,
    balances,
    build_world,
    fund_available,
    make_beneficiary,
    operation_status,
    paid_payment,
    refund,
    request_payout,
    settle,
)

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]

BANK_DATABASE_URL = "postgresql+psycopg://bank_app:bank_app_dev@localhost:55432/bank"
BANK_ACCOUNT = "bank_f1_test"
BANK_SECRET = "f1-synthetic-bank-secret"
AMOUNT = 100_000


class LocalBankTransport:
    """In-process ``BankTransport``: the real signed bank simulator, no HTTP hop."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        reply = apply_transfer(
            self.factory, command=parse_command(request_bytes), signing_secret=BANK_SECRET
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        reply = lookup_transfer(
            self.factory,
            account_public_id=account_id,
            stable_key=stable_key,
            signing_secret=BANK_SECRET,
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    pending: int
    available: int
    receivable: int
    reserved: int
    payout_eligible: int

    @classmethod
    def of(cls, name: str, value: MerchantBalances) -> "Stage":
        return cls(
            name,
            value.pending,
            value.available,
            value.receivable,
            value.reserved,
            value.payout_eligible,
        )


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("f1-refund-vs-payout-tests") as session_factory:
        yield session_factory


@pytest.fixture
def bank_factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(BANK_DATABASE_URL, application_name="f1-payout-bank-test")
    bank_sessions = sessionmaker(bind=engine, expire_on_commit=False, autobegin=False)
    try:
        with bank_sessions() as session, session.begin():
            if (
                session.scalar(select(BankAccount).where(BankAccount.public_id == BANK_ACCOUNT))
                is None
            ):
                session.add(
                    BankAccount(
                        public_id=BANK_ACCOUNT,
                        name="F1 synthetic account",
                        signing_secret_digest=hashlib.sha256(BANK_SECRET.encode()).digest(),
                    )
                )
        yield bank_sessions
    finally:
        engine.dispose()


def _payout_status(factory: sessionmaker[Session], world: World, payout_id: str) -> str:
    with factory() as session, session.begin():
        status = session.scalar(
            select(Payout.status).where(
                Payout.organisation_id == world.organisation_id, Payout.public_id == payout_id
            )
        )
    assert status is not None
    return str(status)


def test_control_refund_without_a_payout_draws_on_available(
    factory: sessionmaker[Session],
) -> None:
    """Sanity check for the drivers: with nothing reserved, AVAILABLE funds the refund."""
    world = build_world(factory, label="f1-control")
    provider = FakeProvider()
    flow = fund_available(factory, world, provider, AMOUNT)
    assert balances(factory, world).available == AMOUNT

    refund_op = refund(factory, world, flow.payment_id, AMOUNT, provider)

    assert operation_status(factory, world, refund_op) == "SUCCEEDED"
    after = balances(factory, world)
    assert (after.available, after.receivable, after.reserved) == (0, 0, 0)


def test_refund_does_not_consume_funds_reserved_for_an_inflight_payout(
    factory: sessionmaker[Session], bank_factory: sessionmaker[Session]
) -> None:
    # Observed on current code (paise): settled available=100000; payout reserved
    # available=100000 reserved=100000; refund SUCCEEDED -> available=0 reserved=100000
    # receivable=0; payout then SUCCEEDED through the bank path -> available=-100000.
    # The customer is refunded 100000 and the merchant is also paid out 100000.
    world = build_world(factory, label="f1-refund-vs-payout")
    provider = FakeProvider()
    trace: list[Stage] = []

    def observe(name: str) -> Stage:
        stage = Stage.of(name, balances(factory, world))
        trace.append(stage)
        # Invariants that must hold at every stage, whatever order the money moved in.
        assert stage.available >= 0, f"AVAILABLE went negative: {trace}"
        assert stage.reserved <= stage.available, f"reserved exceeds AVAILABLE: {trace}"
        assert stage.payout_eligible >= 0, f"payout_eligible went negative: {trace}"
        return stage

    flow = fund_available(factory, world, provider, AMOUNT)
    funded = observe("settled")
    assert (funded.pending, funded.available, funded.reserved) == (0, AMOUNT, 0)

    beneficiary = make_beneficiary(factory, world)
    created = request_payout(factory, world, beneficiary, AMOUNT)
    payout_id = str(json.loads(created.body)["id"])
    reserved = observe("payout reserved")
    assert (reserved.available, reserved.reserved, reserved.payout_eligible) == (AMOUNT, AMOUNT, 0)

    refund_op = refund(factory, world, flow.payment_id, AMOUNT, provider)
    assert operation_status(factory, world, refund_op) == "SUCCEEDED"
    refunded = observe("refunded")
    # The refund must not touch the reserved funds; the platform fronts it as receivable.
    assert refunded.available == AMOUNT, f"refund drew on reserved funds: {trace}"
    assert refunded.receivable == AMOUNT, f"refund was not booked to receivable: {trace}"

    dispatch_payout_attempt(
        factory,
        payout_public_id=payout_id,
        bank_account_id=BANK_ACCOUNT,
        bank_signing_secret=BANK_SECRET,
        transport=LocalBankTransport(bank_factory),
    )
    assert _payout_status(factory, world, payout_id) == "SUCCEEDED"
    finished = observe("payout finalized")
    assert (finished.available, finished.reserved) == (0, 0)


def test_partial_reservation_refund_splits_between_available_and_receivable(
    factory: sessionmaker[Session], bank_factory: sessionmaker[Session]
) -> None:
    """Reserve 60000 of 100000, refund 100000: only the unreserved 40000 comes from AVAILABLE."""
    world = build_world(factory, label="f1-partial-reservation")
    provider = FakeProvider()
    reserved_amount = 60_000

    def observe(name: str) -> Stage:
        stage = Stage.of(name, balances(factory, world))
        assert stage.available >= 0, f"{name}: AVAILABLE went negative: {stage}"
        assert stage.reserved <= stage.available, f"{name}: reserved exceeds AVAILABLE: {stage}"
        return stage

    flow = fund_available(factory, world, provider, AMOUNT)
    beneficiary = make_beneficiary(factory, world)
    created = request_payout(factory, world, beneficiary, reserved_amount)
    payout_id = str(json.loads(created.body)["id"])
    observe("payout reserved")

    refund_op = refund(factory, world, flow.payment_id, AMOUNT, provider)
    assert operation_status(factory, world, refund_op) == "SUCCEEDED"
    refunded = observe("refunded")
    assert (refunded.available, refunded.receivable, refunded.reserved) == (
        reserved_amount,
        reserved_amount,
        reserved_amount,
    )
    with factory() as session, session.begin():
        refund_transaction = session.scalar(
            select(BalanceTransaction).where(
                BalanceTransaction.merchant_account_id == world.merchant_account_id,
                BalanceTransaction.transaction_type == "REFUND",
            )
        )
        assert refund_transaction is not None
        # The projection mirrors the journal: 40000 from AVAILABLE, 60000 owed as receivable.
        assert (
            refund_transaction.pending_delta,
            refund_transaction.available_delta,
            refund_transaction.receivable_delta,
        ) == (0, -(AMOUNT - reserved_amount), reserved_amount)

    dispatch_payout_attempt(
        factory,
        payout_public_id=payout_id,
        bank_account_id=BANK_ACCOUNT,
        bank_signing_secret=BANK_SECRET,
        transport=LocalBankTransport(bank_factory),
    )
    assert _payout_status(factory, world, payout_id) == "SUCCEEDED"
    finished = observe("payout finalized")
    assert (finished.available, finished.reserved, finished.receivable) == (
        0,
        0,
        reserved_amount,
    )


def test_settlement_after_refund_leaves_reservation_covered(
    factory: sessionmaker[Session],
) -> None:
    """Second capture settles after the refund: AVAILABLE stays at or above what is reserved."""
    world = build_world(factory, label="f1-second-capture")
    provider = FakeProvider()
    first = fund_available(factory, world, provider, AMOUNT)
    beneficiary = make_beneficiary(factory, world)
    request_payout(factory, world, beneficiary, AMOUNT)
    paid_payment(factory, world, provider, amount=AMOUNT)
    settle(factory, world)
    refund(factory, world, first.payment_id, AMOUNT, provider)

    after = balances(factory, world)
    assert after.reserved == AMOUNT
    # The refund is funded from the second capture's settled value, not from reserved funds.
    assert after.available >= after.reserved
