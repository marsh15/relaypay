import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.models import WebhookEndpoint, WebhookEndpointVersion
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

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
