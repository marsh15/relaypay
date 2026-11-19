from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from relaypay.payments.models import PaymentIntent
    from relaypay.payouts.models import Payout
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
    failure_code: str | None = None,
) -> MerchantEvent:
    """Append the event for a finalized provider operation (parent: the operation)."""
    data: dict[str, object] = {
        "amount": amount,
        "currency": currency,
        "operationId": operation.public_id,
        "paymentIntentId": payment.public_id,
        "resourceId": resource_public_id,
    }
    if failure_code is not None:
        data["failureCode"] = failure_code
    return _store_event(
        session,
        organisation_id=operation.organisation_id,
        environment_id=operation.environment_id,
        event_type=event_type,
        data=data,
        occurred_at=occurred_at,
        payment_intent_id=payment.id,
        provider_operation_id=operation.id,
    )


def append_payment_expired_event(
    session: Session,
    *,
    payment: PaymentIntent,
    amount: int,
    currency: str,
    occurred_at: datetime | None = None,
) -> MerchantEvent:
    """Append ``payment.expired.v1``. Expiry is local, so the payment itself is the parent."""
    return _store_event(
        session,
        organisation_id=payment.organisation_id,
        environment_id=payment.environment_id,
        event_type="payment.expired.v1",
        data={
            "amount": amount,
            "currency": currency,
            "paymentIntentId": payment.public_id,
        },
        occurred_at=occurred_at,
        payment_intent_id=payment.id,
    )


def append_payout_event(
    session: Session,
    *,
    payout: Payout,
    occurred_at: datetime | None = None,
) -> MerchantEvent:
    """Append ``payout.succeeded.v1`` for a payout the bank has verifiably paid."""
    return _store_event(
        session,
        organisation_id=payout.organisation_id,
        environment_id=payout.environment_id,
        event_type="payout.succeeded.v1",
        data={
            "amount": payout.amount,
            "currency": payout.currency,
            "payoutId": payout.public_id,
        },
        occurred_at=occurred_at,
        payout_id=payout.id,
    )


def _store_event(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    event_type: str,
    data: dict[str, object],
    occurred_at: datetime | None,
    payment_intent_id: uuid.UUID | None = None,
    provider_operation_id: uuid.UUID | None = None,
    payout_id: uuid.UUID | None = None,
) -> MerchantEvent:
    timestamp = occurred_at or datetime.now(UTC)
    event_public_id = new_public_id("evt")
    event_bytes = canonical_json_bytes(
        {
            "id": event_public_id,
            "type": event_type,
            "schemaVersion": 1,
            "occurredAt": timestamp.isoformat(),
            "data": data,
        }
    )
    event = MerchantEvent(
        id=new_uuid(),
        public_id=event_public_id,
        organisation_id=organisation_id,
        environment_id=environment_id,
        payment_intent_id=payment_intent_id,
        provider_operation_id=provider_operation_id,
        payout_id=payout_id,
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
            WebhookEndpointVersion.organisation_id == organisation_id,
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
                organisation_id=organisation_id,
                environment_id=environment_id,
                merchant_event_id=event.id,
                endpoint_version_id=endpoint_version_id,
            )
            for endpoint_version_id in endpoint_version_ids
        ]
    )
    return event
