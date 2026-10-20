"""M16 / D7: platform fee and GST on captures, posted at capture; the merchant is paid net.

The calculator is hand-written by the maintainer, so these ledger tests inject a constant test
double (``FIXED_SPLIT``). It encodes none of the real rounding rules; it only lets the ledger,
settlement and refund plumbing be checked independently. The calculator's own specification
lives in tests/unit/test_fee_calculator_spec.py.
"""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.fees.models import FeeScheduleVersion
from relaypay.fees.service import create_fee_schedule_version
from relaypay.fees.types import FeeBreakdown, FeeScheduleTerms
from relaypay.ledger.models import LedgerAccount, Posting
from relaypay.payments.models import Capture, PaymentIntent
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import (
    World,
    authorize,
    balances,
    build_world,
    capture,
    create_payment,
    operation_status,
    refund,
    settle,
)

pytestmark = [pytest.mark.integration]

GROSS = 100_000
FEE = 2_000
GST = 360
NET = GROSS - FEE - GST


def FIXED_SPLIT(amount: int, _terms: FeeScheduleTerms) -> FeeBreakdown:
    return FeeBreakdown(fee=FEE, gst=GST, net=amount - FEE - GST)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("fees-gst-tests", pool_size=8) as session_factory:
        yield session_factory


def _with_schedule(factory: sessionmaker[Session], label: str) -> tuple[World, FakeProvider]:
    world = build_world(factory, label=label)
    with factory() as session, session.begin():
        create_fee_schedule_version(
            session,
            merchant_account_id=world.merchant_account_id,
            effective_from=datetime.now(UTC) - timedelta(minutes=1),
            mdr_bps=200,
            fixed_paise=0,
        )
    return world, FakeProvider()


def _captured(
    factory: sessionmaker[Session], world: World, provider: FakeProvider, *, with_fee: bool = True
) -> str:
    payment = create_payment(factory, world, amount=GROSS)
    authorize(factory, world, payment, provider)
    operation = capture(
        factory, world, payment, provider, fee_calculator=FIXED_SPLIT if with_fee else None
    )
    assert operation_status(factory, world, operation) == "SUCCEEDED"
    return payment


def _capture_row(factory: sessionmaker[Session], payment: str) -> Capture:
    with factory() as session, session.begin():
        row = session.scalar(
            select(Capture)
            .join(PaymentIntent, PaymentIntent.id == Capture.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
        )
        assert row is not None
        session.expunge(row)
        return row


def _account_balance(factory: sessionmaker[Session], world: World, code: str) -> tuple[str, int]:
    with factory() as session, session.begin():
        account = session.scalar(
            select(LedgerAccount).where(
                LedgerAccount.organisation_id == world.organisation_id,
                LedgerAccount.environment_id == world.environment_id,
                LedgerAccount.merchant_account_id.is_(None),
                LedgerAccount.code == code,
            )
        )
        assert account is not None, code
        credits = session.scalar(
            select(func.coalesce(func.sum(Posting.amount), 0)).where(
                Posting.account_id == account.id, Posting.side == "CREDIT"
            )
        )
        return account.account_type, int(credits or 0)


def test_no_schedule_means_no_fee_and_gross_to_pending(factory: sessionmaker[Session]) -> None:
    world = build_world(factory, label="fees-none")
    provider = FakeProvider()

    payment = _captured(factory, world, provider, with_fee=False)

    row = _capture_row(factory, payment)
    assert (row.fee_amount, row.gst_amount, row.fee_schedule_version_id) == (0, 0, None)
    assert balances(factory, world).pending == GROSS


def test_capture_posts_net_to_merchant_and_fee_and_gst_to_platform(
    factory: sessionmaker[Session],
) -> None:
    world, provider = _with_schedule(factory, "fees-capture")

    payment = _captured(factory, world, provider)

    row = _capture_row(factory, payment)
    assert (row.amount, row.fee_amount, row.gst_amount, row.net_amount) == (GROSS, FEE, GST, NET)
    assert row.fee_schedule_version_id is not None
    assert balances(factory, world).pending == NET
    assert _account_balance(factory, world, "PLATFORM_FEE_REVENUE") == ("INCOME", FEE)
    assert _account_balance(factory, world, "GST_PAYABLE_LIABILITY") == ("LIABILITY", GST)


def test_settlement_moves_the_net_to_available(factory: sessionmaker[Session]) -> None:
    world, provider = _with_schedule(factory, "fees-settle")
    _captured(factory, world, provider)

    settle(factory, world)

    after = balances(factory, world)
    assert (after.pending, after.available) == (0, NET)


def test_refund_retains_fee_and_gst_and_merchant_bears_the_full_refund(
    factory: sessionmaker[Session],
) -> None:
    world, provider = _with_schedule(factory, "fees-refund")
    payment = _captured(factory, world, provider)

    operation = refund(factory, world, payment, GROSS, provider)

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    after = balances(factory, world)
    # The customer gets the gross back; the merchant only ever held the net, so it owes the rest.
    assert (after.pending, after.available, after.receivable) == (0, 0, FEE + GST)
    assert _account_balance(factory, world, "PLATFORM_FEE_REVENUE") == ("INCOME", FEE)


def test_inconsistent_calculator_output_is_refused(factory: sessionmaker[Session]) -> None:
    world, provider = _with_schedule(factory, "fees-bad-split")
    payment = create_payment(factory, world, amount=GROSS)
    authorize(factory, world, payment, provider)

    def broken(amount: int, _terms: FeeScheduleTerms) -> FeeBreakdown:
        return FeeBreakdown(fee=FEE, gst=GST, net=amount)  # does not sum to gross

    with pytest.raises(RuntimeError, match="inconsistent split"):
        capture(factory, world, payment, fee_calculator=broken)
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(Capture)
                .join(PaymentIntent, PaymentIntent.id == Capture.payment_intent_id)
                .where(PaymentIntent.public_id == payment)
            )
            == 0
        )


def test_schedule_versions_are_immutable_and_future_versions_are_not_applied(
    factory: sessionmaker[Session],
) -> None:
    world, provider = _with_schedule(factory, "fees-versions")
    with factory() as session, session.begin():
        future = create_fee_schedule_version(
            session,
            merchant_account_id=world.merchant_account_id,
            effective_from=datetime.now(UTC) + timedelta(days=1),
            mdr_bps=500,
            fixed_paise=0,
        )
        assert future.version == 2

    payment = _captured(factory, world, provider)
    with factory() as session, session.begin():
        applied = session.scalar(
            select(FeeScheduleVersion.version).where(
                FeeScheduleVersion.id == _capture_row(factory, payment).fee_schedule_version_id
            )
        )
    assert applied == 1

    with pytest.raises(DBAPIError, match="immutable"), factory() as session, session.begin():
        session.execute(
            update(FeeScheduleVersion)
            .where(FeeScheduleVersion.merchant_account_id == world.merchant_account_id)
            .values(mdr_bps=0)
        )


def test_concurrent_first_fees_create_each_platform_account_once(
    factory: sessionmaker[Session],
) -> None:
    world, provider = _with_schedule(factory, "fees-concurrent")
    payments = []
    for _ in range(4):
        payment = create_payment(factory, world, amount=GROSS)
        authorize(factory, world, payment, provider)
        payments.append(payment)
    barrier = threading.Barrier(len(payments))

    def run(payment: str) -> str:
        barrier.wait()
        return capture(factory, world, payment, provider, fee_calculator=FIXED_SPLIT)

    with ThreadPoolExecutor(max_workers=len(payments)) as pool:
        operations = list(pool.map(run, payments))

    assert all(operation_status(factory, world, op) == "SUCCEEDED" for op in operations)
    with factory() as session, session.begin():
        counts = dict(
            session.execute(
                select(LedgerAccount.code, func.count())
                .where(
                    LedgerAccount.organisation_id == world.organisation_id,
                    LedgerAccount.code.in_(["PLATFORM_FEE_REVENUE", "GST_PAYABLE_LIABILITY"]),
                )
                .group_by(LedgerAccount.code)
            ).all()
        )
    assert counts == {"PLATFORM_FEE_REVENUE": 1, "GST_PAYABLE_LIABILITY": 1}
    assert _account_balance(factory, world, "PLATFORM_FEE_REVENUE")[1] == FEE * len(payments)
