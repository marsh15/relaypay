"""M17 T17.5: merchant events gain non-capture parents and new event types.

New types: void (cancelled), expiry, failure and payout success.

A merchant event has exactly one parent: a provider operation (authorize, capture, void, refund),
a payout, or, for expiry, the payment alone (expiry is local and has no provider operation).
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.event_delivery.materializer import materialize_deliveries
from relaypay.event_delivery.models import (
    EventRecipient,
    MerchantEvent,
    WebhookEndpoint,
    WebhookEndpointVersion,
)
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.expiry import expire_authorizations
from relaypay.payments.models import Authorization, PaymentIntent
from relaypay.payouts.models import Payout
from relaypay.payouts.service import dispatch_payout_attempt
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from scripts.chaos.driver import BANK_ACCOUNT, BANK_SECRET, LocalBankTransport, bank_factory
from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.world import (
    World,
    authorize,
    build_world,
    create_payment,
    fund_available,
    make_beneficiary,
    request_payout,
    void,
)

pytestmark = [pytest.mark.integration]

ALL_TYPES = [
    "payment.authorized.v1",
    "payment.captured.v1",
    "payment.failed.v1",
    "payment.cancelled.v1",
    "payment.expired.v1",
    "refund.succeeded.v1",
    "payout.succeeded.v1",
]


_SUBSCRIBED: list[uuid.UUID] = []


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("merchant-events-v2-tests", pool_size=8) as session_factory:
        yield session_factory
        # The materializer is global: leave no unmaterialised recipients behind for other tests.
        while _SUBSCRIBED:
            materialize_deliveries(session_factory, organisation_id=_SUBSCRIBED.pop())


def _subscribe(factory: sessionmaker[Session], world: World) -> uuid.UUID:
    """Subscribe one active endpoint to every event type; returns its version id."""
    _SUBSCRIBED.append(world.organisation_id)
    with factory() as session, session.begin():
        endpoint = WebhookEndpoint(
            public_id=new_public_id("wh"),
            organisation_id=world.organisation_id,
            name="Events v2 receiver",
            status="ACTIVE",
        )
        session.add(endpoint)
        session.flush()
        version = WebhookEndpointVersion(
            public_id=new_public_id("whv"),
            organisation_id=world.organisation_id,
            webhook_endpoint_id=endpoint.id,
            version=1,
            url="http://receiver:8002/webhooks/relaypay",
            encrypted_secret=b"encrypted-test-secret",
            subscribed_event_types=ALL_TYPES,
            active_from=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(version)
        session.flush()
        return version.id


def _events(factory: sessionmaker[Session], world: World, event_type: str) -> list[MerchantEvent]:
    with factory() as session, session.begin():
        rows = list(
            session.scalars(
                select(MerchantEvent).where(
                    MerchantEvent.organisation_id == world.organisation_id,
                    MerchantEvent.event_type == event_type,
                )
            )
        )
        session.expunge_all()
        return rows


def _recipients(factory: sessionmaker[Session], event: MerchantEvent) -> int:
    with factory() as session, session.begin():
        return int(
            session.scalar(
                select(func.count())
                .select_from(EventRecipient)
                .where(EventRecipient.merchant_event_id == event.id)
            )
            or 0
        )


def _authorized(factory: sessionmaker[Session], label: str) -> tuple[World, str, FakeProvider]:
    world = build_world(factory, label=label)
    provider = FakeProvider()
    payment = create_payment(factory, world)
    authorize(factory, world, payment, provider)
    return world, payment, provider


def test_void_success_emits_payment_cancelled_to_subscribers(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="events-void")
    version_id = _subscribe(factory, world)
    provider = FakeProvider()
    payment = create_payment(factory, world)
    authorize(factory, world, payment, provider)

    operation = void(factory, world, payment, provider)

    (event,) = _events(factory, world, "payment.cancelled.v1")
    assert event.provider_operation_id is not None and event.payout_id is None
    body = json.loads(event.event_bytes)
    assert body["type"] == "payment.cancelled.v1"
    assert body["data"]["paymentIntentId"] == payment
    assert body["data"]["operationId"] == operation
    assert _recipients(factory, event) == 1
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(EventRecipient.endpoint_version_id).where(
                    EventRecipient.merchant_event_id == event.id
                )
            )
            == version_id
        )


def test_declined_authorization_emits_payment_failed(factory: sessionmaker[Session]) -> None:
    world = build_world(factory, label="events-failed")
    _subscribe(factory, world)
    payment = create_payment(factory, world)
    authorize(factory, world, payment, FakeProvider(default=Outcome.DECLINE))

    assert _events(factory, world, "payment.authorized.v1") == []
    (event,) = _events(factory, world, "payment.failed.v1")
    body = json.loads(event.event_bytes)
    assert body["data"]["paymentIntentId"] == payment
    assert body["data"]["failureCode"]


def test_expiry_emits_one_payment_expired_event_even_if_the_sweep_repeats(
    factory: sessionmaker[Session],
) -> None:
    world, payment, _ = _authorized(factory, "events-expired")
    _subscribe(factory, world)
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

    assert expire_authorizations(factory, organisation_id=world.organisation_id) == 1
    assert expire_authorizations(factory, organisation_id=world.organisation_id) == 0

    (event,) = _events(factory, world, "payment.expired.v1")
    assert event.provider_operation_id is None and event.payout_id is None
    assert json.loads(event.event_bytes)["data"]["paymentIntentId"] == payment
    assert _recipients(factory, event) == 1


def test_payout_success_emits_payout_succeeded_with_a_payout_parent(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="events-payout")
    _subscribe(factory, world)
    fund_available(factory, world, FakeProvider(), 100_000)
    beneficiary = make_beneficiary(factory, world)
    payout_id = str(json.loads(request_payout(factory, world, beneficiary, 60_000).body)["id"])
    with bank_factory(pool_size=4) as bank:
        dispatch_payout_attempt(
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=LocalBankTransport(bank),
        )

    (event,) = _events(factory, world, "payout.succeeded.v1")
    assert event.payment_intent_id is None and event.provider_operation_id is None
    with factory() as session, session.begin():
        payout = session.scalar(select(Payout).where(Payout.public_id == payout_id))
        assert payout is not None and payout.status == "SUCCEEDED"
        assert event.payout_id == payout.id
    body = json.loads(event.event_bytes)
    assert body["data"]["payoutId"] == payout_id
    assert body["data"]["amount"] == 60_000
    assert _recipients(factory, event) == 1


def _insert_event(
    factory: sessionmaker[Session],
    world: World,
    *,
    event_type: str,
    payment_id: uuid.UUID | None,
    operation_id: uuid.UUID | None,
    payout_id: uuid.UUID | None,
) -> None:
    payload = json.dumps({"nonce": uuid.uuid4().hex}).encode()
    with factory() as session, session.begin():
        session.add(
            MerchantEvent(
                id=new_uuid(),
                public_id=new_public_id("evt"),
                organisation_id=world.organisation_id,
                environment_id=world.environment_id,
                payment_intent_id=payment_id,
                provider_operation_id=operation_id,
                payout_id=payout_id,
                event_type=event_type,
                schema_version=1,
                event_bytes=payload,
                event_sha256=b"\x00" * 32,
                occurred_at=datetime.now(UTC),
            )
        )


def test_database_requires_exactly_one_parent(factory: sessionmaker[Session]) -> None:
    world, payment, _ = _authorized(factory, "events-parent-check")
    with factory() as session, session.begin():
        payment_id = session.scalar(
            select(PaymentIntent.id).where(PaymentIntent.public_id == payment)
        )
    assert payment_id is not None

    # No parent at all, for a type that needs one.
    with pytest.raises(DBAPIError):
        _insert_event(
            factory,
            world,
            event_type="payment.captured.v1",
            payment_id=payment_id,
            operation_id=None,
            payout_id=None,
        )
    # A payout event may not claim a payment.
    with pytest.raises(DBAPIError):
        _insert_event(
            factory,
            world,
            event_type="payout.succeeded.v1",
            payment_id=payment_id,
            operation_id=None,
            payout_id=new_uuid(),
        )
    # An unknown event type is still refused.
    with pytest.raises(DBAPIError):
        _insert_event(
            factory,
            world,
            event_type="payment.mystery.v1",
            payment_id=payment_id,
            operation_id=None,
            payout_id=None,
        )
