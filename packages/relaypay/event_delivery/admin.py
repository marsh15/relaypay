import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError, not_found
from relaypay.event_delivery.models import (
    EventRecipient,
    MerchantEvent,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpointVersion,
)
from relaypay.ids import new_public_id


def read_delivery(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    delivery_public_id: str,
) -> dict[str, Any]:
    with factory() as session, session.begin():
        row = session.execute(
            select(WebhookDelivery, EventRecipient, MerchantEvent, WebhookEndpointVersion)
            .join(EventRecipient, EventRecipient.id == WebhookDelivery.event_recipient_id)
            .join(MerchantEvent, MerchantEvent.id == EventRecipient.merchant_event_id)
            .join(
                WebhookEndpointVersion,
                WebhookEndpointVersion.id == EventRecipient.endpoint_version_id,
            )
            .where(
                WebhookDelivery.organisation_id == organisation_id,
                WebhookDelivery.public_id == delivery_public_id,
            )
        ).one_or_none()
        if row is None:
            raise not_found("Webhook delivery")
        delivery, _, event, endpoint_version = row
        attempts = list(
            session.scalars(
                select(WebhookDeliveryAttempt)
                .where(
                    WebhookDeliveryAttempt.organisation_id == organisation_id,
                    WebhookDeliveryAttempt.webhook_delivery_id == delivery.id,
                )
                .order_by(WebhookDeliveryAttempt.sequence)
                .limit(100)
            )
        )
        return {
            "id": delivery.public_id,
            "status": delivery.status,
            "attemptCount": delivery.attempt_count,
            "event": {
                "id": event.public_id,
                "type": event.event_type,
                "sha256": event.event_sha256.hex(),
            },
            "endpointVersion": {
                "id": endpoint_version.public_id,
                "url": endpoint_version.url,
            },
            "replayOfDeliveryId": str(delivery.replay_of_delivery_id)
            if delivery.replay_of_delivery_id
            else None,
            "deliveredAt": delivery.delivered_at.isoformat() if delivery.delivered_at else None,
            "deadLetteredAt": delivery.dead_lettered_at.isoformat()
            if delivery.dead_lettered_at
            else None,
            "attempts": [
                {
                    "sequence": attempt.sequence,
                    "result": attempt.result,
                    "httpStatus": attempt.response_http_status,
                    "eventSha256": attempt.event_sha256.hex(),
                    "safeErrorCode": attempt.safe_error_code,
                    "startedAt": attempt.started_at.isoformat(),
                    "completedAt": attempt.completed_at.isoformat(),
                }
                for attempt in attempts
            ],
        }


def replay_delivery(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    delivery_public_id: str,
) -> str:
    with factory() as session, session.begin():
        original = session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.organisation_id == organisation_id,
                WebhookDelivery.public_id == delivery_public_id,
            )
            .with_for_update()
        )
        if original is None:
            raise not_found("Webhook delivery")
        if original.status not in {"DELIVERED", "DEAD_LETTER"}:
            raise RelayPayError(
                code="DELIVERY_NOT_TERMINAL",
                message="Only a terminal webhook delivery can be replayed",
                http_status=409,
            )
        replay = WebhookDelivery(
            public_id=new_public_id("del"),
            organisation_id=organisation_id,
            event_recipient_id=original.event_recipient_id,
            replay_of_delivery_id=original.id,
            status="PENDING",
            attempt_count=0,
            next_attempt_at=datetime.now(UTC),
        )
        session.add(replay)
        session.flush()
        return replay.public_id
