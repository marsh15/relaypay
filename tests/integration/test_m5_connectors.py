import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.connectors.crypto import decrypt_credential
from relaypay.connectors.models import (
    Connector,
    ConnectorCredentialVersion,
    ConnectorHealthObservation,
    ConnectorVersion,
    InboundWebhookAttempt,
    InboundWebhookEvent,
)
from relaypay.connectors.protocols import ConnectorError, ConnectorRequest
from relaypay.connectors.service import (
    accept_inbound_webhook,
    activate_connector_version,
    claim_inbound_webhook,
    create_connector_version,
    process_inbound_claim,
    record_connector_result,
    verify_connector_version,
)
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.mock_commerce.models import CommerceAccount, CommerceOrder, CommercePaymentLink
from relaypay.mock_commerce.service import create_order, link_payment, synchronize_event
from relaypay.provider_operations.service_types import ProviderObservation
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from tests.integration.test_m3_merchant_balances import _identity

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]

COMMERCE_URL = "postgresql+psycopg://commerce_app:commerce_app_dev@localhost:55432/commerce"
ENCRYPTION_KEY = "m5-test-connector-encryption"
WEBHOOK_SECRET = "m5-synthetic-inbound-secret"


class HealthyAdapter:
    capability = "payments.effects"

    def request(self, command: ConnectorRequest) -> ProviderObservation:
        return ProviderObservation(200, command.body, {})

    def lookup(self, stable_key: str) -> ProviderObservation:
        return ProviderObservation(200, stable_key.encode(), {})

    def validate(self, observation: ProviderObservation) -> bool:
        return observation.status_code == 200

    def classify(self, observation: ProviderObservation | None) -> ConnectorError | None:
        return (
            None
            if observation is not None and observation.status_code == 200
            else ConnectorError("TEMPORARY", "UNHEALTHY")
        )

    def health(self) -> ProviderObservation:
        return ProviderObservation(
            200,
            b'{"status":"healthy"}',
            {"X-RateLimit-Remaining": "99"},
        )


def _signature(timestamp: str, body: bytes) -> str:
    return (
        "v1="
        + hmac.new(
            WEBHOOK_SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    )


def test_connector_credentials_are_versioned_encrypted_verified_and_rotated() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            first = create_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=f"payment-{uuid.uuid4().hex}",
                kind="PAYMENT",
                base_url="http://provider.test",
                capabilities=["payments.effects"],
                timeout_ms=1000,
                encryption_key=ENCRYPTION_KEY,
            )
        verify_connector_version(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            version_public_id=first.version_public_id,
            adapter=HealthyAdapter(),
        )
        with factory() as session, session.begin():
            activate_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                version_public_id=first.version_public_id,
            )
            connector = session.scalar(
                select(Connector).where(Connector.public_id == first.connector_public_id)
            )
            assert connector is not None
            second = create_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=connector.reference,
                kind="PAYMENT",
                base_url="http://provider-v2.test",
                capabilities=["payments.effects"],
                timeout_ms=1500,
                encryption_key=ENCRYPTION_KEY,
            )
        verify_connector_version(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            version_public_id=second.version_public_id,
            adapter=HealthyAdapter(),
        )
        with factory() as session, session.begin():
            activate_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                version_public_id=second.version_public_id,
            )
            versions = list(
                session.scalars(
                    select(ConnectorVersion)
                    .where(ConnectorVersion.connector_id == connector.id)
                    .order_by(ConnectorVersion.version)
                )
            )
            assert [(item.version, item.status) for item in versions] == [
                (1, "REVOKED"),
                (2, "ACTIVE"),
            ]
            credential = session.scalar(
                select(ConnectorCredentialVersion).where(
                    ConnectorCredentialVersion.connector_version_id == versions[1].id
                )
            )
            assert credential is not None
            assert second.credential.encode() not in credential.encrypted_secret
            assert (
                decrypt_credential(credential.encrypted_secret, ENCRYPTION_KEY) == second.credential
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ConnectorHealthObservation)
                    .where(ConnectorHealthObservation.connector_id == connector.id)
                )
                == 2
            )
    finally:
        engine.dispose()


def test_circuit_state_is_postgresql_durable() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            issued = create_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=f"bank-{uuid.uuid4().hex}",
                kind="BANK",
                base_url="http://bank.test",
                capabilities=["payouts.transfers"],
                timeout_ms=1000,
                encryption_key=ENCRYPTION_KEY,
            )
            connector = session.scalar(
                select(Connector).where(Connector.public_id == issued.connector_public_id)
            )
            assert connector is not None
            for _ in range(3):
                record_connector_result(
                    session,
                    connector=connector,
                    latency_ms=1000,
                    error=ConnectorError("TEMPORARY", "CONNECTOR_TIMEOUT"),
                )
            assert connector.circuit_state == "OPEN"
        with factory() as session, session.begin():
            connector = session.scalar(
                select(Connector).where(Connector.public_id == issued.connector_public_id)
            )
            assert connector is not None
            record_connector_result(session, connector=connector, latency_ms=3, error=None)
            assert (connector.circuit_state, connector.consecutive_failures) == ("CLOSED", 0)
    finally:
        engine.dispose()


def test_inbound_signature_replay_dedup_attempts_and_commerce_sync() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    commerce_engine = build_engine(COMMERCE_URL, application_name="m5-commerce-test")
    commerce_factory = sessionmaker(bind=commerce_engine, expire_on_commit=False, autobegin=False)
    try:
        with factory() as session, session.begin():
            issued = create_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=f"commerce-{uuid.uuid4().hex}",
                kind="COMMERCE",
                base_url="http://commerce.test",
                capabilities=["commerce.orders"],
                timeout_ms=1000,
                encryption_key=ENCRYPTION_KEY,
            )
        with commerce_factory() as session, session.begin():
            account_id = f"commerce_{uuid.uuid4().hex}"
            session.add(
                CommerceAccount(
                    public_id=account_id,
                    name="M5 commerce test",
                    signing_secret_digest=hashlib.sha256(WEBHOOK_SECRET.encode()).digest(),
                )
            )
        order = create_order(
            commerce_factory,
            account_public_id=account_id,
            external_reference=f"order-{uuid.uuid4().hex}",
            total_amount=50_000,
        )
        payment_id = f"pay_{uuid.uuid4().hex}"
        link_payment(
            commerce_factory,
            order_public_id=order.public_id,
            relaypay_payment_id=payment_id,
            amount=50_000,
        )
        body = json.dumps(
            {"paymentId": payment_id, "type": "payment.captured.v1"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        timestamp = str(int(datetime.now(UTC).timestamp()))
        event, replayed = accept_inbound_webhook(
            factory,
            connector_public_id=issued.connector_public_id,
            provider_event_id=f"commerce-event-{uuid.uuid4().hex}",
            timestamp_text=timestamp,
            signature=_signature(timestamp, body),
            body=body,
            secret=WEBHOOK_SECRET,
            replay_seconds=300,
        )
        replay, was_replayed = accept_inbound_webhook(
            factory,
            connector_public_id=issued.connector_public_id,
            provider_event_id=event.provider_event_id,
            timestamp_text=timestamp,
            signature=_signature(timestamp, body),
            body=body,
            secret=WEBHOOK_SECRET,
            replay_seconds=300,
        )
        assert not replayed and was_replayed and replay.id == event.id
        claim = claim_inbound_webhook(factory)
        assert claim is not None
        assert process_inbound_claim(
            factory,
            claim,
            handler=lambda payload, event_id: synchronize_event(
                commerce_factory, payload, event_id
            ),
        )
        with commerce_factory() as session, session.begin():
            current_order = session.get(CommerceOrder, order.id)
            link = session.scalar(
                select(CommercePaymentLink).where(
                    CommercePaymentLink.relaypay_payment_id == payment_id
                )
            )
            assert current_order is not None and link is not None
            assert (current_order.status, link.status) == ("PAID", "PAID")
        with factory() as session, session.begin():
            stored = session.get(InboundWebhookEvent, event.id)
            assert stored is not None and stored.status == "PROCESSED"
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(InboundWebhookAttempt)
                    .where(InboundWebhookAttempt.inbound_webhook_event_id == event.id)
                )
                == 1
            )
        stale = str(int((datetime.now(UTC) - timedelta(hours=1)).timestamp()))
        with pytest.raises(RelayPayError) as raised:
            accept_inbound_webhook(
                factory,
                connector_public_id=issued.connector_public_id,
                provider_event_id="stale",
                timestamp_text=stale,
                signature=_signature(stale, body),
                body=body,
                secret=WEBHOOK_SECRET,
                replay_seconds=300,
            )
        assert raised.value.code == "WEBHOOK_REPLAY_WINDOW_EXCEEDED"
        with pytest.raises(RelayPayError) as invalid:
            accept_inbound_webhook(
                factory,
                connector_public_id=issued.connector_public_id,
                provider_event_id="invalid-signature",
                timestamp_text=timestamp,
                signature="v1=" + ("0" * 64),
                body=body,
                secret=WEBHOOK_SECRET,
                replay_seconds=300,
            )
        assert invalid.value.code == "INVALID_WEBHOOK_SIGNATURE"
        failed_event, _ = accept_inbound_webhook(
            factory,
            connector_public_id=issued.connector_public_id,
            provider_event_id=f"failed-{uuid.uuid4().hex}",
            timestamp_text=timestamp,
            signature=_signature(timestamp, body),
            body=body,
            secret=WEBHOOK_SECRET,
            replay_seconds=300,
        )
        failed_claim = claim_inbound_webhook(factory)
        assert failed_claim is not None

        def fail_processing(_: bytes, __: str) -> None:
            raise RuntimeError("synthetic commerce outage")

        assert not process_inbound_claim(
            factory,
            failed_claim,
            handler=fail_processing,
            max_attempts=1,
        )
        with factory() as session, session.begin():
            dead = session.get(InboundWebhookEvent, failed_event.id)
            assert dead is not None and dead.status == "DEAD_LETTER"
    finally:
        commerce_engine.dispose()
        engine.dispose()
