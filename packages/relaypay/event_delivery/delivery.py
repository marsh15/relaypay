import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx2
from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import Session, sessionmaker

from relaypay.event_delivery.crypto import decrypt_webhook_secret
from relaypay.event_delivery.models import (
    EventRecipient,
    MerchantEvent,
    WebhookDelivery,
    WebhookDeliveryAttempt,
    WebhookEndpointVersion,
)
from relaypay.ids import new_uuid

MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    delivery_id: uuid.UUID
    lease_token: uuid.UUID


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    url: str
    event_id: str
    event_bytes: bytes
    event_sha256: bytes
    secret: str


@dataclass(frozen=True, slots=True)
class DeliveryResponse:
    status_code: int


class WebhookTransport(Protocol):
    def send(self, *, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResponse: ...


class HTTPWebhookTransport:
    def __init__(self, *, allowed_url: str, timeout_seconds: float = 3.0) -> None:
        self._allowed_url = allowed_url
        self._timeout = timeout_seconds

    def send(self, *, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResponse:
        if url != self._allowed_url:
            raise ValueError("webhook destination is not allowlisted")
        response = httpx2.post(
            url,
            content=body,
            headers=headers,
            timeout=self._timeout,
            follow_redirects=False,
        )
        return DeliveryResponse(status_code=response.status_code)


def claim_delivery(
    factory: sessionmaker[Session],
    *,
    lease_seconds: int = 30,
    organisation_id: uuid.UUID | None = None,
) -> DeliveryClaim | None:
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        delivery = session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.organisation_id == organisation_id
                if organisation_id is not None
                else true(),
                or_(
                    and_(
                        WebhookDelivery.status.in_(("PENDING", "RETRY_WAIT")),
                        WebhookDelivery.next_attempt_at <= now,
                    ),
                    and_(
                        WebhookDelivery.status == "DELIVERING",
                        WebhookDelivery.lease_expires_at <= now,
                    ),
                ),
            )
            .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if delivery is None:
            return None
        token = new_uuid()
        delivery.status = "DELIVERING"
        delivery.lease_token = token
        delivery.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return DeliveryClaim(delivery.id, token)


def _load_request(
    factory: sessionmaker[Session], claim: DeliveryClaim, *, encryption_key: str
) -> DeliveryRequest | None:
    with factory() as session, session.begin():
        row = session.execute(
            select(WebhookDelivery, MerchantEvent, WebhookEndpointVersion)
            .join(EventRecipient, EventRecipient.id == WebhookDelivery.event_recipient_id)
            .join(MerchantEvent, MerchantEvent.id == EventRecipient.merchant_event_id)
            .join(
                WebhookEndpointVersion,
                WebhookEndpointVersion.id == EventRecipient.endpoint_version_id,
            )
            .where(
                WebhookDelivery.id == claim.delivery_id,
                WebhookDelivery.status == "DELIVERING",
                WebhookDelivery.lease_token == claim.lease_token,
            )
        ).one_or_none()
        if row is None:
            return None
        _, event, version = row
        return DeliveryRequest(
            version.url,
            event.public_id,
            event.event_bytes,
            event.event_sha256,
            decrypt_webhook_secret(version.encrypted_secret, encryption_key),
        )


def _classify(status: int | None) -> str:
    if status is None:
        return "TRANSPORT_ERROR"
    if 200 <= status < 300:
        return "ACKNOWLEDGED"
    if status in (408, 429) or status >= 500:
        return "RETRYABLE"
    return "PERMANENT"


def _record_attempt(
    factory: sessionmaker[Session],
    claim: DeliveryClaim,
    request: DeliveryRequest,
    *,
    timestamp: int,
    status_code: int | None,
    result: str,
    started_at: datetime,
    safe_error_code: str | None,
) -> bool:
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        delivery = session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.id == claim.delivery_id,
                WebhookDelivery.status == "DELIVERING",
                WebhookDelivery.lease_token == claim.lease_token,
            )
            .with_for_update()
        )
        if delivery is None:
            return False
        sequence = delivery.attempt_count + 1
        attempt = WebhookDeliveryAttempt(
            id=new_uuid(),
            organisation_id=delivery.organisation_id,
            webhook_delivery_id=delivery.id,
            sequence=sequence,
            lease_token=claim.lease_token,
            request_timestamp=timestamp,
            event_sha256=request.event_sha256,
            response_http_status=status_code,
            result=result,
            safe_error_code=safe_error_code,
            started_at=started_at,
            completed_at=now,
        )
        session.add(attempt)
        session.flush([attempt])
        delivery.attempt_count = sequence
        delivery.lease_token = None
        delivery.lease_expires_at = None
        if result == "ACKNOWLEDGED":
            delivery.status = "DELIVERED"
            delivery.delivered_at = now
        elif result == "PERMANENT" or sequence >= MAX_ATTEMPTS:
            delivery.status = "DEAD_LETTER"
            delivery.dead_lettered_at = now
        else:
            delivery.status = "RETRY_WAIT"
            delivery.next_attempt_at = now + timedelta(seconds=min(60, 2 ** (sequence - 1)))
        return True


def deliver_claim(
    factory: sessionmaker[Session],
    claim: DeliveryClaim,
    *,
    encryption_key: str,
    transport: WebhookTransport,
) -> bool:
    request = _load_request(factory, claim, encryption_key=encryption_key)
    if request is None:
        return False
    timestamp = int(datetime.now(UTC).timestamp())
    signature = hmac.new(
        request.secret.encode(), f"{timestamp}.".encode() + request.event_bytes, hashlib.sha256
    ).hexdigest()
    started_at = datetime.now(UTC)
    status: int | None = None
    safe_error: str | None = None
    try:
        status = transport.send(
            url=request.url,
            body=request.event_bytes,
            headers={
                "Content-Type": "application/json",
                "X-RelayPay-Event-Id": request.event_id,
                "X-RelayPay-Timestamp": str(timestamp),
                "X-RelayPay-Signature": f"v1={signature}",
            },
        ).status_code
    except Exception:
        safe_error = "WEBHOOK_TRANSPORT_ERROR"
    return _record_attempt(
        factory,
        claim,
        request,
        timestamp=timestamp,
        status_code=status,
        result=_classify(status),
        started_at=started_at,
        safe_error_code=safe_error,
    )


def run_delivery_batch(
    factory: sessionmaker[Session],
    *,
    encryption_key: str,
    transport: WebhookTransport,
    batch_size: int = 50,
) -> int:
    processed = 0
    for _ in range(batch_size):
        claim = claim_delivery(factory)
        if claim is None:
            break
        if deliver_claim(factory, claim, encryption_key=encryption_key, transport=transport):
            processed += 1
    return processed
