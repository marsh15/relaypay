"""Migration 0020: the database refuses payout reservations or AVAILABLE debits that overdraw.

Application code already serializes these writers on the merchant-account lock (and F1 is fixed
there). These tests bypass that code with raw writes, which is what a future bug would do, and
show the commit is rejected with SQLSTATE 23514 while every legitimate flow still commits.
"""

import json
import uuid
from collections.abc import Iterator

import pytest
from relaypay.ids import new_uuid
from relaypay.ledger.service import account, post_journal
from relaypay.payouts.models import Payout, PayoutReservationHistory
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

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

pytestmark = [pytest.mark.integration]

FUNDED = 100_000


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("payout-overdraft-guard-tests", pool_size=4) as session_factory:
        yield session_factory


def _funded_world(factory: sessionmaker[Session], label: str) -> World:
    world = build_world(factory, label=label)
    fund_available(factory, world, FakeProvider(), FUNDED)
    assert balances(factory, world).available == FUNDED
    return world


def _reserve(factory: sessionmaker[Session], world: World, amount: int) -> uuid.UUID:
    created = request_payout(factory, world, make_beneficiary(factory, world), amount)
    public_id = str(json.loads(created.body)["id"])
    with factory() as session, session.begin():
        payout_id = session.scalar(select(Payout.id).where(Payout.public_id == public_id))
    assert payout_id is not None
    return payout_id


def _raw_reservation(
    factory: sessionmaker[Session], world: World, payout_id: uuid.UUID, amount: int
) -> None:
    with factory() as session, session.begin():
        sequence = session.scalar(
            select(func.max(PayoutReservationHistory.sequence)).where(
                PayoutReservationHistory.payout_id == payout_id
            )
        )
        session.add(
            PayoutReservationHistory(
                id=new_uuid(),
                organisation_id=world.organisation_id,
                environment_id=world.environment_id,
                merchant_account_id=world.merchant_account_id,
                payout_id=payout_id,
                sequence=int(sequence or 0) + 1,
                action="RESERVED",
                amount_delta=amount,
                reason_code="TEST_RAW_RESERVATION",
            )
        )


def _raw_available_debit(factory: sessionmaker[Session], world: World, amount: int) -> None:
    """A balanced journal that moves ``amount`` out of AVAILABLE into the receivable."""
    with factory() as session, session.begin():
        available = account(
            session,
            world.organisation_id,
            world.environment_id,
            "AVAILABLE_PAYABLE_LIABILITY",
            merchant_account_id=world.merchant_account_id,
        )
        receivable = account(
            session,
            world.organisation_id,
            world.environment_id,
            "MERCHANT_RECEIVABLE_ASSET",
            merchant_account_id=world.merchant_account_id,
        )
        post_journal(
            session,
            organisation_id=world.organisation_id,
            environment_id=world.environment_id,
            merchant_account_id=world.merchant_account_id,
            provider_operation_id=None,
            journal_type="COMPENSATION",  # type: ignore[arg-type]
            reference_type="test_raw_debit",
            reference_id=new_uuid(),
            entries=[(available, "DEBIT", amount), (receivable, "CREDIT", amount)],
        )


def test_reservation_beyond_available_is_rejected_at_commit(
    factory: sessionmaker[Session],
) -> None:
    world = _funded_world(factory, "guard-reservation")
    payout_id = _reserve(factory, world, 60_000)

    with pytest.raises(IntegrityError, match="exceed available payable"):
        _raw_reservation(factory, world, payout_id, 40_001)

    _raw_reservation(factory, world, payout_id, 40_000)  # exactly covered: allowed
    assert balances(factory, world).reserved == FUNDED


def test_available_debit_below_reserved_is_rejected_at_commit(
    factory: sessionmaker[Session],
) -> None:
    world = _funded_world(factory, "guard-debit")
    _reserve(factory, world, 70_000)

    with pytest.raises(IntegrityError, match="exceed available payable"):
        _raw_available_debit(factory, world, 30_001)

    _raw_available_debit(factory, world, 30_000)  # leaves exactly the reserved amount
    after = balances(factory, world)
    assert (after.available, after.reserved) == (70_000, 70_000)


def test_available_can_never_go_negative_even_without_reservations(
    factory: sessionmaker[Session],
) -> None:
    world = _funded_world(factory, "guard-negative")

    with pytest.raises(IntegrityError, match="exceed available payable"):
        _raw_available_debit(factory, world, FUNDED + 1)

    assert balances(factory, world).available == FUNDED
