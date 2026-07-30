"""Run a synthetic M5 connector rotation, inbound webhook, and commerce synchronization proof."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

from relaypay.config import get_settings
from relaypay.connectors.protocols import ConnectorError, ConnectorRequest
from relaypay.connectors.service import (
    accept_inbound_webhook,
    activate_connector_version,
    claim_inbound_webhook,
    create_connector_version,
    process_inbound_claim,
    verify_connector_version,
)
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import Environment, Organisation, User
from relaypay.identity.security import Principal
from relaypay.mock_commerce.service import create_order, link_payment, synchronize_event
from relaypay.provider_operations.service_types import ProviderObservation
from sqlalchemy import select


class DemoHealthAdapter:
    capability = "commerce.orders"

    def request(self, command: ConnectorRequest) -> ProviderObservation:
        return ProviderObservation(200, command.body, {})

    def lookup(self, stable_key: str) -> ProviderObservation:
        return ProviderObservation(200, stable_key.encode(), {})

    def validate(self, observation: ProviderObservation) -> bool:
        return observation.status_code == 200

    def classify(self, observation: ProviderObservation | None) -> ConnectorError | None:
        return (
            None
            if observation and observation.status_code == 200
            else ConnectorError("TEMPORARY", "UNHEALTHY")
        )

    def health(self) -> ProviderObservation:
        return ProviderObservation(200, b'{"status":"healthy"}', {})


def main() -> None:
    settings = get_settings()
    relay_engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="m5-connector-demo",
    )
    commerce_engine = build_engine(
        settings.COMMERCE_DATABASE_URL.get_secret_value(),
        application_name="m5-commerce-demo",
    )
    relay = build_session_factory(relay_engine)
    commerce = build_session_factory(commerce_engine)
    try:
        with relay() as session, session.begin():
            organisation = session.scalar(
                select(Organisation).where(Organisation.name == "Northstar Demo")
            )
            user = session.scalar(
                select(User).where(User.email_normalized == "admin@northstar.test")
            )
            assert organisation is not None and user is not None
            environment = session.scalar(
                select(Environment).where(
                    Environment.organisation_id == organisation.id,
                    Environment.environment_type == "TEST",
                )
            )
            assert environment is not None
            principal = Principal(
                kind="SESSION",
                organisation_id=organisation.id,
                organisation_public_id=organisation.public_id,
                environment_id=None,
                environment_public_id=None,
                display_name=user.display_name,
                scopes=frozenset(),
                membership_role="ORGANISATION_ADMIN",
                user_id=user.id,
            )
            issued = create_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=f"m5-demo-{uuid.uuid4().hex}",
                kind="COMMERCE",
                base_url=settings.COMMERCE_BASE_URL,
                capabilities=["commerce.orders"],
                timeout_ms=1000,
                encryption_key=settings.CONNECTOR_CREDENTIAL_ENCRYPTION_KEY.get_secret_value(),
            )
        verify_connector_version(
            relay,
            principal=principal,
            environment_public_id=environment.public_id,
            version_public_id=issued.version_public_id,
            adapter=DemoHealthAdapter(),
        )
        with relay() as session, session.begin():
            activate_connector_version(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                version_public_id=issued.version_public_id,
            )
        order = create_order(
            commerce,
            account_public_id=settings.COMMERCE_ACCOUNT_ID,
            external_reference=f"m5-demo-order-{uuid.uuid4().hex}",
            total_amount=12_500,
        )
        payment_id = f"pay_{uuid.uuid4().hex}"
        link_payment(
            commerce,
            order_public_id=order.public_id,
            relaypay_payment_id=payment_id,
            amount=12_500,
        )
        payload = json.dumps(
            {"paymentId": payment_id, "type": "payment.captured.v1"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        timestamp = str(int(datetime.now(UTC).timestamp()))
        signature = (
            "v1="
            + hmac.new(
                settings.COMMERCE_CONTROL_SECRET.get_secret_value().encode(),
                timestamp.encode() + b"." + payload,
                hashlib.sha256,
            ).hexdigest()
        )
        event, _ = accept_inbound_webhook(
            relay,
            connector_public_id=issued.connector_public_id,
            provider_event_id=f"evt_{uuid.uuid4().hex}",
            timestamp_text=timestamp,
            signature=signature,
            body=payload,
            secret=settings.COMMERCE_CONTROL_SECRET.get_secret_value(),
            replay_seconds=settings.INBOUND_WEBHOOK_REPLAY_SECONDS,
        )
        claim = claim_inbound_webhook(relay)
        assert claim is not None
        assert process_inbound_claim(
            relay,
            claim,
            handler=lambda body, event_id: synchronize_event(commerce, body, event_id),
        )
        print(
            "M5 connector proof passed:",
            f"connector={issued.connector_public_id}",
            f"version={issued.version}",
            f"inbound_event={event.public_id}",
            f"commerce_order={order.public_id}",
            "status=PAID",
        )
    finally:
        commerce_engine.dispose()
        relay_engine.dispose()


if __name__ == "__main__":
    main()
