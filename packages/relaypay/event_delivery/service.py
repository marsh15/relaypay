import hashlib
from datetime import UTC, datetime

from sqlalchemy import literal, or_, select
from sqlalchemy.orm import Session

from relaypay.event_delivery.models import (
    EventRecipient,
    MerchantEvent,
    WebhookEndpoint,
    WebhookEndpointVersion,
)
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import PaymentIntent
from relaypay.provider_operations.models import ProviderOperation


def append_merchant_event(
    session: Session,
    *,
    payment: PaymentIntent,
    operation: ProviderOperation,
    resource_public_id: str,
    amount: int,
    currency: str,
    event_type: str,
    occurred_at: datetime | None = None,
) -> MerchantEvent:
    timestamp = occurred_at or datetime.now(UTC)
    event_public_id = new_public_id("evt")
    event_bytes = canonical_json_bytes(
        {
            "id": event_public_id,
            "type": event_type,
            "schemaVersion": 1,
            "occurredAt": timestamp.isoformat(),
            "data": {
                "amount": amount,
                "currency": currency,
                "operationId": operation.public_id,
                "paymentIntentId": payment.public_id,
                "resourceId": resource_public_id,
            },
        }
    )
    event = MerchantEvent(
        id=new_uuid(),
        public_id=event_public_id,
        organisation_id=operation.organisation_id,
        payment_intent_id=payment.id,
        provider_operation_id=operation.id,
        event_type=event_type,
        schema_version=1,
        event_bytes=event_bytes,
        event_sha256=hashlib.sha256(event_bytes).digest(),
        occurred_at=timestamp,
    )
    session.add(event)
    session.flush([event])
    endpoint_version_ids = session.scalars(
        select(WebhookEndpointVersion.id)
        .join(
            WebhookEndpoint,
            WebhookEndpoint.id == WebhookEndpointVersion.webhook_endpoint_id,
        )
        .where(
            WebhookEndpointVersion.organisation_id == operation.organisation_id,
            WebhookEndpoint.status == "ACTIVE",
            WebhookEndpointVersion.active_from <= timestamp,
            or_(
                WebhookEndpointVersion.active_until.is_(None),
                WebhookEndpointVersion.active_until > timestamp,
            ),
            WebhookEndpointVersion.subscribed_event_types.any(literal(event_type)),
        )
        .order_by(WebhookEndpointVersion.id)
    ).all()
    session.add_all(
        [
            EventRecipient(
                organisation_id=operation.organisation_id,
                merchant_event_id=event.id,
                endpoint_version_id=endpoint_version_id,
            )
            for endpoint_version_id in endpoint_version_ids
        ]
    )
    return event
