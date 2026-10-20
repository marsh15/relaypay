"""Migration 0019: a raw UPDATE of payment_intents.status accepts exactly ALLOWED.

Every one of the 81 (old, new) pairs is tried with plain SQL, bypassing ``transition()``. Each
attempt runs in a SAVEPOINT inside one outer transaction that is rolled back at the end, so
nothing leaks into the shared database. Illegal pairs fail with the dedicated SQLSTATE RP001.
"""

import uuid
from collections.abc import Iterator
from itertools import product

import pytest
from relaypay.payments.models import Customer, PaymentIntent, PaymentStatusHistory
from relaypay.payments.transitions import ALLOWED, PaymentStatus
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.world import World, build_world, create_payment

pytestmark = pytest.mark.integration

TRANSITION_SQLSTATE = "RP001"


@pytest.fixture(scope="module")
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("status-trigger-tests") as session_factory:
        yield session_factory


@pytest.fixture(scope="module")
def world(factory: sessionmaker[Session]) -> World:
    return build_world(factory, label="status-trigger")


def _insert_payment(session: Session, world: World, status: str) -> uuid.UUID:
    customer = session.scalar(
        select(Customer).where(
            Customer.organisation_id == world.organisation_id,
            Customer.public_id == world.customer_public_id,
        )
    )
    assert customer is not None
    tag = uuid.uuid4().hex
    payment = PaymentIntent(
        public_id=f"pay_{tag}",
        organisation_id=world.organisation_id,
        environment_id=world.environment_id,
        customer_id=customer.id,
        merchant_account_id=world.merchant_account_id,
        merchant_reference=f"trigger-{tag}",
        amount=1000,
        status=status,  # INSERT is not guarded; only UPDATE OF status is
    )
    session.add(payment)
    session.flush()
    return payment.id


def test_raw_update_accepts_exactly_allowed(factory: sessionmaker[Session], world: World) -> None:
    accepted: set[tuple[str, str]] = set()
    rejected: set[tuple[str, str]] = set()
    with factory() as session:
        outer = session.begin()
        try:
            ids = {
                status: _insert_payment(session, world, status.value) for status in PaymentStatus
            }
            for source, target in product(PaymentStatus, PaymentStatus):
                savepoint = session.begin_nested()
                try:
                    session.execute(
                        text("UPDATE payment_intents SET status = :target WHERE id = :id"),
                        {"target": target.value, "id": ids[source]},
                    )
                except DBAPIError as error:
                    assert getattr(error.orig, "sqlstate", None) == TRANSITION_SQLSTATE, error
                    assert "illegal payment status transition" in str(error.orig)
                    rejected.add((source.value, target.value))
                else:
                    accepted.add((source.value, target.value))
                finally:
                    savepoint.rollback()
        finally:
            outer.rollback()
    assert accepted == ALLOWED
    assert len(rejected) == 81 - len(ALLOWED)
    assert accepted | rejected == {(a.value, b.value) for a in PaymentStatus for b in PaymentStatus}


def test_unknown_status_and_null_are_rejected(factory: sessionmaker[Session], world: World) -> None:
    with factory() as session:
        outer = session.begin()
        try:
            payment_id = _insert_payment(session, world, PaymentStatus.CREATED.value)
            for value, sqlstate in (("bogus", TRANSITION_SQLSTATE), (None, TRANSITION_SQLSTATE)):
                savepoint = session.begin_nested()
                try:
                    session.execute(
                        text("UPDATE payment_intents SET status = :value WHERE id = :id"),
                        {"value": value, "id": payment_id},
                    )
                except DBAPIError as error:
                    assert getattr(error.orig, "sqlstate", None) == sqlstate, error
                else:
                    raise AssertionError(f"status {value!r} must be rejected")
                finally:
                    savepoint.rollback()
            # An insert with a status outside the enum hits the CHECK (23514), not the trigger.
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError) as check:
                _insert_payment(session, world, "bogus")
            assert getattr(check.value.orig, "sqlstate", None) == "23514"
            savepoint.rollback()
        finally:
            outer.rollback()


def test_updates_not_touching_status_are_unguarded(
    factory: sessionmaker[Session], world: World
) -> None:
    with factory() as session:
        outer = session.begin()
        try:
            payment_id = _insert_payment(session, world, PaymentStatus.REFUNDED.value)
            result = session.execute(
                text("UPDATE payment_intents SET updated_at = now() WHERE id = :id"),
                {"id": payment_id},
            )
            assert result.rowcount == 1
        finally:
            outer.rollback()


def test_history_rows_are_immutable(factory: sessionmaker[Session], world: World) -> None:
    payment_public_id = create_payment(factory, world)
    with factory() as session:
        outer = session.begin()
        try:
            row = session.scalar(
                select(PaymentStatusHistory)
                .join(PaymentIntent, PaymentIntent.id == PaymentStatusHistory.payment_intent_id)
                .where(PaymentIntent.public_id == payment_public_id)
            )
            assert row is not None
            for statement in (
                "UPDATE payment_status_history SET reason = 'x' WHERE id = :id",
                "DELETE FROM payment_status_history WHERE id = :id",
            ):
                savepoint = session.begin_nested()
                with pytest.raises(DBAPIError) as error:
                    session.execute(text(statement), {"id": row.id})
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
                savepoint.rollback()
        finally:
            outer.rollback()
