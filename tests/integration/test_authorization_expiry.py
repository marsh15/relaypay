"""M16: an authorization hold expires; an uncaptured payment moves to ``expired``."""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from relaypay.errors import RelayPayError
from relaypay.payments.expiry import expire_authorizations
from relaypay.payments.models import Authorization, Capture, PaymentIntent, PaymentStatusHistory
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import (
    World,
    authorize,
    build_world,
    capture,
    create_payment,
    dispatch,
    operation_status,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("authorization-expiry-tests", pool_size=8) as session_factory:
        yield session_factory


def _authorized(
    factory: sessionmaker[Session], label: str, *, hold_seconds: int | None = None
) -> tuple[World, str, FakeProvider]:
    world = build_world(factory, label=label)
    provider = FakeProvider()
    payment = create_payment(factory, world)
    authorize(factory, world, payment, provider, hold_seconds=hold_seconds)
    return world, payment, provider


def _authorization(factory: sessionmaker[Session], payment: str) -> Authorization:
    with factory() as session, session.begin():
        row = session.scalar(
            select(Authorization)
            .join(PaymentIntent, PaymentIntent.id == Authorization.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
        )
        assert row is not None
        session.expunge(row)
        return row


def _status(factory: sessionmaker[Session], payment: str) -> str:
    with factory() as session, session.begin():
        return str(
            session.scalar(select(PaymentIntent.status).where(PaymentIntent.public_id == payment))
        )


def _lapse(factory: sessionmaker[Session], payment: str) -> None:
    with factory() as session, session.begin():
        session.execute(
            update(Authorization)
            .where(
                Authorization.payment_intent_id
                == select(PaymentIntent.id)
                .where(PaymentIntent.public_id == payment)
                .scalar_subquery()
            )
            .values(expires_at=func.now() - timedelta(seconds=1))
        )


def test_succeeded_authorization_records_its_hold_and_expiry(
    factory: sessionmaker[Session],
) -> None:
    _, payment, _ = _authorized(factory, "expiry-recorded", hold_seconds=3600)

    authorization = _authorization(factory, payment)

    assert authorization.hold_seconds == 3600
    assert authorization.authorized_at is not None and authorization.expires_at is not None
    assert authorization.expires_at - authorization.authorized_at == timedelta(seconds=3600)


def test_default_hold_is_seven_days(factory: sessionmaker[Session]) -> None:
    _, payment, _ = _authorized(factory, "expiry-default")
    assert _authorization(factory, payment).hold_seconds == 7 * 24 * 3600


def test_lapsed_uncaptured_payment_expires_and_cannot_be_captured(
    factory: sessionmaker[Session],
) -> None:
    world, payment, _ = _authorized(factory, "expiry-lapsed")
    assert expire_authorizations(factory, organisation_id=world.organisation_id) == 0

    _lapse(factory, payment)
    assert expire_authorizations(factory, organisation_id=world.organisation_id) == 1

    assert _status(factory, payment) == "expired"
    with factory() as session, session.begin():
        last = session.scalar(
            select(PaymentStatusHistory)
            .join(PaymentIntent, PaymentIntent.id == PaymentStatusHistory.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
            .order_by(PaymentStatusHistory.status_version.desc())
            .limit(1)
        )
        assert last is not None
        assert (last.from_status, last.to_status, last.reason, last.actor) == (
            "authorised",
            "expired",
            "AUTHORIZATION_EXPIRED",
            "EXPIRY_WORKER",
        )
    with pytest.raises(RelayPayError) as raised:
        capture(factory, world, payment)
    assert raised.value.code == "PAYMENT_NOT_CAPTURABLE"
    assert expire_authorizations(factory, organisation_id=world.organisation_id) == 0


def test_capture_is_refused_once_the_hold_lapses_even_before_the_poller_runs(
    factory: sessionmaker[Session],
) -> None:
    world, payment, _ = _authorized(factory, "expiry-before-poller")
    _lapse(factory, payment)

    with pytest.raises(RelayPayError) as raised:
        capture(factory, world, payment)

    assert raised.value.code == "AUTHORIZATION_EXPIRED"
    assert _status(factory, payment) == "authorised"


def test_capture_in_flight_prevents_expiry(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "expiry-in-flight")
    operation = capture(factory, world, payment)  # committed, provider call still pending
    _lapse(factory, payment)

    assert expire_authorizations(factory, organisation_id=world.organisation_id) == 0

    dispatch(factory, world, operation, provider)
    assert operation_status(factory, world, operation) == "SUCCEEDED"
    assert _status(factory, payment) == "captured"


@pytest.mark.parametrize("round_", range(6))
def test_capture_racing_expiry_has_exactly_one_winner(
    factory: sessionmaker[Session], round_: int
) -> None:
    world, payment, _ = _authorized(factory, f"expiry-race-{round_}")
    with factory() as session, session.begin():
        # Expire a moment from now so both sides see a live hold until the barrier releases.
        session.execute(
            update(Authorization)
            .where(
                Authorization.payment_intent_id
                == select(PaymentIntent.id)
                .where(PaymentIntent.public_id == payment)
                .scalar_subquery()
            )
            .values(expires_at=func.now() + timedelta(milliseconds=300))
        )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    # Both sides fire at the expiry boundary: capture's and expiry's "expires_at <= now()"
    # checks race, and the payment row lock must let exactly one of them win.
    def capture_side() -> None:
        barrier.wait()
        threading.Event().wait(0.3)
        try:
            capture(factory, world, payment)
            outcomes.append("captured")
        except RelayPayError as error:
            outcomes.append(error.code)

    def expiry_side() -> None:
        barrier.wait()
        threading.Event().wait(0.3)
        outcomes.append(
            f"expired:{expire_authorizations(factory, organisation_id=world.organisation_id)}"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(capture_side), pool.submit(expiry_side)]:
            future.result()

    with factory() as session, session.begin():
        captures = session.scalar(
            select(func.count())
            .select_from(Capture)
            .join(PaymentIntent, PaymentIntent.id == Capture.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
        )
    status = _status(factory, payment)
    if captures:
        assert status == "authorised"  # capture initiated (not yet dispatched): expiry skipped
        assert sorted(outcomes) == ["captured", "expired:0"]
    else:
        assert status in {"expired", "authorised"}
        # Capture lost: refused as expired (before or after the poller marked the payment).
        assert any(o in {"AUTHORIZATION_EXPIRED", "PAYMENT_NOT_CAPTURABLE"} for o in outcomes)
        if status == "authorised":  # expiry ran before the hold lapsed; next sweep expires it
            assert expire_authorizations(factory, organisation_id=world.organisation_id) == 1
