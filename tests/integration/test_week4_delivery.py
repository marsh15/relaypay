import hashlib
import hmac
from datetime import UTC, datetime

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.crypto import encrypt_webhook_secret
from relaypay.event_delivery.delivery import (
    DeliveryResponse,
    claim_delivery,
    deliver_claim,
)
from relaypay.event_delivery.materializer import materialize_deliveries
from relaypay.event_delivery.models import (
    MerchantEvent,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpoint,
    WebhookEndpointVersion,
)
from relaypay.ids import new_public_id
from relaypay.receiver.models import ReceivedEvent
from relaypay.receiver.service import ReceiverValidationError, receive_event
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
RECEIVER_URL = "postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay"
ENCRYPTION_KEY = "week-4-test-encryption-key"
WEBHOOK_SECRET = "week-4-test-webhook-secret"


class InProcessReceiverTransport:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def send(self, *, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResponse:
        assert url == "http://receiver:8002/webhooks/relaypay"
        with self.factory() as session, session.begin():
            receive_event(
                session,
                body=body,
                event_id=headers["X-RelayPay-Event-Id"],
                timestamp_text=headers["X-RelayPay-Timestamp"],
                signature=headers["X-RelayPay-Signature"],
                secret=WEBHOOK_SECRET,
            )
        return DeliveryResponse(200)


class RetryableTransport:
    def send(self, *, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResponse:
        return DeliveryResponse(503)


@pytest.fixture
def factories() -> tuple[sessionmaker[Session], sessionmaker[Session]]:
    relaypay_engine = build_engine(DATABASE_URL, application_name="week4-delivery-tests")
    receiver_engine = build_engine(RECEIVER_URL, application_name="week4-receiver-tests")
    result = (
        build_session_factory(relaypay_engine),
        build_session_factory(receiver_engine),
    )
    yield result
    relaypay_engine.dispose()
    receiver_engine.dispose()


def _create_event(factory: sessionmaker[Session]) -> tuple[object, str]:
    organisation_id, payment_id = _new_payment(factory)
    with factory() as session, session.begin():
        endpoint = WebhookEndpoint(
            public_id=new_public_id("wh"),
            organisation_id=organisation_id,
            name="Bundled receiver",
            status="ACTIVE",
        )
        session.add(endpoint)
        session.flush()
        session.add(
            WebhookEndpointVersion(
                public_id=new_public_id("whv"),
                organisation_id=organisation_id,
                webhook_endpoint_id=endpoint.id,
                version=1,
                url="http://receiver:8002/webhooks/relaypay",
                encrypted_secret=encrypt_webhook_secret(WEBHOOK_SECRET, ENCRYPTION_KEY),
                subscribed_event_types=["payment.authorized.v1"],
                active_from=datetime.now(UTC),
            )
        )
    operation_id = _authorize(factory, organisation_id, payment_id)
    _dispatch(factory, organisation_id, operation_id, DeterministicTransport())
    with factory() as session, session.begin():
        event_id = session.scalar(
            select(MerchantEvent.public_id).where(MerchantEvent.organisation_id == organisation_id)
        )
        assert event_id is not None
    return organisation_id, event_id


def test_signed_delivery_receiver_dedup_and_attempt_evidence(
    factories: tuple[sessionmaker[Session], sessionmaker[Session]],
) -> None:
    relaypay, receiver = factories
    organisation_id, event_id = _create_event(relaypay)
    assert materialize_deliveries(relaypay) >= 1
    claim = claim_delivery(relaypay, organisation_id=organisation_id)
    assert claim is not None
    transport = InProcessReceiverTransport(receiver)
    assert deliver_claim(relaypay, claim, encryption_key=ENCRYPTION_KEY, transport=transport)

    with relaypay() as session, session.begin():
        delivery = session.get(WebhookDelivery, claim.delivery_id)
        assert delivery is not None
        assert delivery.status == "DELIVERED"
        assert delivery.attempt_count == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(WebhookDeliveryAttempt)
                .where(WebhookDeliveryAttempt.webhook_delivery_id == delivery.id)
            )
            == 1
        )
        attempt_id = session.scalar(
            select(WebhookDeliveryAttempt.id).where(
                WebhookDeliveryAttempt.webhook_delivery_id == delivery.id
            )
        )
        assert attempt_id is not None
        event_bytes = session.scalar(
            select(MerchantEvent.event_bytes).where(MerchantEvent.public_id == event_id)
        )
        assert event_bytes is not None
    timestamp = str(int(datetime.now(UTC).timestamp()))
    signature = (
        "v1="
        + hmac.new(
            WEBHOOK_SECRET.encode(), timestamp.encode() + b"." + event_bytes, hashlib.sha256
        ).hexdigest()
    )
    with receiver() as session, session.begin():
        replay = receive_event(
            session,
            body=event_bytes,
            event_id=event_id,
            timestamp_text=timestamp,
            signature=signature,
            secret=WEBHOOK_SECRET,
        )
        assert replay.duplicate
        assert replay.delivery_count == 2
    with receiver() as session, session.begin():
        received = session.get(ReceivedEvent, event_id)
        assert received is not None
        assert received.delivery_count == 2
    with pytest.raises(DBAPIError), relaypay() as session, session.begin():
        attempt = session.get(WebhookDeliveryAttempt, attempt_id)
        assert attempt is not None
        attempt.safe_error_code = "TAMPERED"


def test_expired_lease_is_reclaimed_and_receiver_rejects_tampering(
    factories: tuple[sessionmaker[Session], sessionmaker[Session]],
) -> None:
    relaypay, receiver = factories
    organisation_id, event_id = _create_event(relaypay)
    materialize_deliveries(relaypay)
    first = claim_delivery(relaypay, organisation_id=organisation_id, lease_seconds=0)
    assert first is not None
    reclaimed = claim_delivery(relaypay, organisation_id=organisation_id)
    assert reclaimed is not None
    assert reclaimed.delivery_id == first.delivery_id
    assert reclaimed.lease_token != first.lease_token
    assert not deliver_claim(
        relaypay,
        first,
        encryption_key=ENCRYPTION_KEY,
        transport=InProcessReceiverTransport(receiver),
    )
    assert deliver_claim(
        relaypay,
        reclaimed,
        encryption_key=ENCRYPTION_KEY,
        transport=InProcessReceiverTransport(receiver),
    )

    timestamp = str(int(datetime.now(UTC).timestamp()))
    tampered = b'{"id":"' + event_id.encode() + b'","tampered":true}'
    with pytest.raises(ReceiverValidationError), receiver() as session, session.begin():
        receive_event(
            session,
            body=tampered,
            event_id=event_id,
            timestamp_text=timestamp,
            signature="v1=" + ("0" * 64),
            secret=WEBHOOK_SECRET,
        )

    with receiver() as session, session.begin():
        received = session.get(ReceivedEvent, event_id)
        assert received is not None
        assert received.delivery_count == 1


def test_retry_budget_ends_in_dead_letter(
    factories: tuple[sessionmaker[Session], sessionmaker[Session]],
) -> None:
    relaypay, _ = factories
    organisation_id, _ = _create_event(relaypay)
    materialize_deliveries(relaypay)
    delivery_id = None
    for sequence in range(1, 6):
        claim = claim_delivery(relaypay, organisation_id=organisation_id)
        assert claim is not None
        delivery_id = claim.delivery_id
        assert deliver_claim(
            relaypay,
            claim,
            encryption_key=ENCRYPTION_KEY,
            transport=RetryableTransport(),
        )
        with relaypay() as session, session.begin():
            delivery = session.get(WebhookDelivery, claim.delivery_id)
            assert delivery is not None
            assert delivery.attempt_count == sequence
            if sequence < 5:
                assert delivery.status == "RETRY_WAIT"
                delivery.next_attempt_at = datetime.now(UTC)
    with relaypay() as session, session.begin():
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == "DEAD_LETTER"
        assert delivery.dead_lettered_at is not None
