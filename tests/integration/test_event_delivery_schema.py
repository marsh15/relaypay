import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.materializer import (
    materialize_deliveries,
    materialize_delivery_batch,
)
from relaypay.event_delivery.models import (
    EventRecipient,
    MerchantEvent,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEndpointVersion,
)
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_week3_provider_recovery import (
    DeterministicTransport,
    _authorize,
    _dispatch,
    _new_payment,
)

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(DATABASE_URL, application_name="event-delivery-schema-tests")
    resolved = build_session_factory(engine)
    yield resolved
    engine.dispose()


def _endpoint_version(
    factory: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID, datetime]:
    active_from = datetime.now(UTC)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="Delivery schema tests", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        endpoint = WebhookEndpoint(
            public_id=new_public_id("wh"),
            organisation_id=organisation.id,
            name="Bundled receiver",
            status="ACTIVE",
        )
        session.add(endpoint)
        session.flush()
        version = WebhookEndpointVersion(
            public_id=new_public_id("whv"),
            organisation_id=organisation.id,
            webhook_endpoint_id=endpoint.id,
            version=1,
            url="http://receiver:8002/webhooks/relaypay",
            encrypted_secret=b"encrypted-test-secret",
            subscribed_event_types=[
                "payment.authorized.v1",
                "payment.captured.v1",
                "refund.succeeded.v1",
            ],
            active_from=active_from,
        )
        session.add(version)
        session.flush()
        return endpoint.id, version.id, active_from


def test_endpoint_version_is_immutable_except_for_one_way_closure(
    factory: sessionmaker[Session],
) -> None:
    _, version_id, active_from = _endpoint_version(factory)

    with pytest.raises(DBAPIError), factory() as session, session.begin():
        version = session.get(WebhookEndpointVersion, version_id)
        assert version is not None
        version.url = "http://attacker.invalid/webhook"

    closed_at = active_from + timedelta(hours=1)
    with factory() as session, session.begin():
        version = session.get(WebhookEndpointVersion, version_id)
        assert version is not None
        version.active_until = closed_at

    with pytest.raises(DBAPIError), factory() as session, session.begin():
        version = session.get(WebhookEndpointVersion, version_id)
        assert version is not None
        version.active_until = closed_at + timedelta(hours=1)

    with pytest.raises(DBAPIError), factory() as session, session.begin():
        version = session.get(WebhookEndpointVersion, version_id)
        assert version is not None
        session.delete(version)


def test_endpoint_version_rejects_cross_tenant_binding(
    factory: sessionmaker[Session],
) -> None:
    endpoint_id, _, active_from = _endpoint_version(factory)
    with factory() as session, session.begin():
        other = Organisation(public_id=new_public_id("org"), name="Other tenant", status="ACTIVE")
        session.add(other)
        session.flush()
        other_id = other.id

    with pytest.raises(DBAPIError), factory() as session, session.begin():
        session.add(
            WebhookEndpointVersion(
                public_id=new_public_id("whv"),
                organisation_id=other_id,
                webhook_endpoint_id=endpoint_id,
                version=2,
                url="http://receiver:8002/webhooks/relaypay",
                encrypted_secret=b"encrypted-test-secret",
                subscribed_event_types=["payment.captured.v1"],
                active_from=active_from,
            )
        )


def _active_version(factory: sessionmaker[Session], organisation_id: uuid.UUID) -> uuid.UUID:
    with factory() as session, session.begin():
        endpoint = WebhookEndpoint(
            public_id=new_public_id("wh"),
            organisation_id=organisation_id,
            name="Bundled receiver",
            status="ACTIVE",
        )
        session.add(endpoint)
        session.flush()
        version = WebhookEndpointVersion(
            public_id=new_public_id("whv"),
            organisation_id=organisation_id,
            webhook_endpoint_id=endpoint.id,
            version=1,
            url="http://receiver:8002/webhooks/relaypay",
            encrypted_secret=b"encrypted-test-secret",
            subscribed_event_types=["payment.authorized.v1"],
            active_from=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.add(version)
        session.flush()
        return version.id


def test_finalizer_snapshots_recipient_and_materializer_is_crash_safe(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    endpoint_version_id = _active_version(factory, organisation_id)
    operation_id = _authorize(factory, organisation_id, payment_id)
    _dispatch(factory, organisation_id, operation_id, DeterministicTransport())

    with factory() as session, session.begin():
        operation_event = session.scalar(
            select(MerchantEvent).where(MerchantEvent.organisation_id == organisation_id)
        )
        assert operation_event is not None
        recipient = session.scalar(
            select(EventRecipient).where(EventRecipient.merchant_event_id == operation_event.id)
        )
        assert recipient is not None
        assert recipient.endpoint_version_id == endpoint_version_id
        recipient_id = recipient.id

    _active_version(factory, organisation_id)
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventRecipient)
                .where(EventRecipient.merchant_event_id == operation_event.id)
            )
            == 1
        )

    with pytest.raises(RuntimeError), factory() as session, session.begin():
        assert materialize_delivery_batch(session) == 1
        raise RuntimeError("simulated crash before materializer commit")

    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(WebhookDelivery)
                .where(WebhookDelivery.event_recipient_id == recipient_id)
            )
            == 0
        )

    assert materialize_deliveries(factory) == 1
    assert materialize_deliveries(factory) == 0

    with pytest.raises(DBAPIError), factory() as session, session.begin():
        recipient = session.get(EventRecipient, recipient_id)
        assert recipient is not None
        session.delete(recipient)
