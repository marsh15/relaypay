"""M16 / D6: one capture per payment, of any positive amount up to the authorized amount."""

from collections.abc import Iterator

import pytest
from relaypay.errors import RelayPayError
from relaypay.ledger.models import Journal, Posting
from relaypay.payments.models import Authorization, Capture, PaymentIntent
from relaypay.provider_operations.models import ProviderOperation
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import (
    World,
    authorize,
    build_world,
    capture,
    create_payment,
    operation_status,
    refund,
)

pytestmark = [pytest.mark.integration]

AUTHORIZED = 100_000


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("partial-capture-tests", pool_size=4) as session_factory:
        yield session_factory


def _authorized(factory: sessionmaker[Session], label: str) -> tuple[World, str, FakeProvider]:
    world = build_world(factory, label=label)
    provider = FakeProvider()
    payment = create_payment(factory, world, amount=AUTHORIZED)
    authorize(factory, world, payment, provider)
    return world, payment, provider


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


def test_partial_capture_posts_only_the_captured_amount(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "partial-capture")

    operation = capture(factory, world, payment, provider, amount=60_000)

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    captured = _capture_row(factory, payment)
    assert (captured.amount, captured.status) == (60_000, "SUCCEEDED")
    with factory() as session, session.begin():
        debits = session.scalar(
            select(func.sum(Posting.amount))
            .join(Journal, Journal.id == Posting.journal_id)
            .where(Journal.id == captured.journal_id, Posting.side == "DEBIT")
        )
        status = session.scalar(
            select(PaymentIntent.status).where(PaymentIntent.public_id == payment)
        )
        sent_amount = session.scalar(
            select(ProviderOperation.provider_request_bytes).where(
                ProviderOperation.public_id == operation
            )
        )
    assert debits == 60_000
    assert status == "captured"
    assert sent_amount is not None and b'"amount":60000' in sent_amount
    provider.assert_exactly_once()


def test_capture_defaults_to_the_full_authorized_amount(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "full-capture")

    capture(factory, world, payment, provider)

    assert _capture_row(factory, payment).amount == AUTHORIZED


@pytest.mark.parametrize("amount", [0, -1, AUTHORIZED + 1])
def test_capture_amount_must_be_positive_and_within_the_authorization(
    factory: sessionmaker[Session], amount: int
) -> None:
    world, payment, _ = _authorized(factory, f"bad-capture-{amount}")

    with pytest.raises(RelayPayError) as raised:
        capture(factory, world, payment, amount=amount)

    assert raised.value.code == "CAPTURE_AMOUNT_EXCEEDS_AUTHORIZED"
    assert raised.value.details == {"authorizedAmount": AUTHORIZED}


def test_second_capture_for_a_different_amount_is_rejected(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "second-capture")
    first = capture(factory, world, payment, provider, amount=60_000)

    with pytest.raises(RelayPayError) as raised:
        capture(factory, world, payment, amount=70_000)
    assert raised.value.code == "CAPTURE_ALREADY_EXISTS"

    # A new key for the same amount converges on the existing capture, as before.
    assert capture(factory, world, payment, amount=60_000) == first


def test_refunds_are_capped_at_the_captured_not_the_authorized_amount(
    factory: sessionmaker[Session],
) -> None:
    world, payment, provider = _authorized(factory, "partial-refund-cap")
    capture(factory, world, payment, provider, amount=60_000)

    with pytest.raises(RelayPayError) as raised:
        refund(factory, world, payment, 60_001)
    assert raised.value.code == "REFUND_AMOUNT_EXCEEDS_AVAILABLE"

    operation = refund(factory, world, payment, 60_000, provider)
    assert operation_status(factory, world, operation) == "SUCCEEDED"
    with factory() as session, session.begin():
        status = session.scalar(
            select(PaymentIntent.status).where(PaymentIntent.public_id == payment)
        )
    assert status == "refunded"


def test_database_rejects_a_capture_above_its_authorization(
    factory: sessionmaker[Session],
) -> None:
    world, payment, _ = _authorized(factory, "db-capture-guard")
    capture(factory, world, payment, amount=60_000)

    with (
        pytest.raises(IntegrityError, match="exceeds the authorized amount"),
        factory() as session,
        session.begin(),
    ):
        authorized = session.scalar(
            select(Authorization.amount)
            .join(PaymentIntent, PaymentIntent.id == Authorization.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
        )
        assert authorized == AUTHORIZED
        session.execute(
            update(Capture)
            .where(Capture.organisation_id == world.organisation_id)
            .values(amount=AUTHORIZED + 1)
        )
