"""The provider simulator's signed webhook push (M17).

Each effect enqueues one ``effect.updated`` event in the effect's own transaction, so an event
exists exactly when its effect does. ``push_webhook_batch`` delivers due events at least once:
a crash after sending but before recording the result sends the event again, which RelayPay's
inbox deduplicates by event id. The chaos profile may drop, duplicate or delay an event.

Wire format (what ``relaypay.psp.inbox.verify_simulator_webhook`` checks):
``X-Provider-Event-Id``, ``X-Provider-Timestamp`` (unix seconds) and ``X-Provider-Signature`` =
``"v1=" + hex(HMAC-SHA256(secret, timestamp + "." + body))``.
"""

import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_uuid
from relaypay.mock_provider.chaos import account_profile, webhook_chaos
from relaypay.mock_provider.models import ProviderWebhookOutbox

WebhookTransport = Callable[[str, dict[str, str], bytes], int]
MAX_PUSH_ATTEMPTS = 8
RETRY_CAP_SECONDS = 60


def sign_webhook(body: bytes, timestamp: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"v1={digest.hexdigest()}"


def enqueue_effect_webhook(
    session: Session,
    *,
    account_id: uuid.UUID,
    effect_id: uuid.UUID,
    stable_key: str,
    effect_body: bytes,
    now: datetime,
) -> ProviderWebhookOutbox:
    """Add the event for one effect to the session (the caller's effect transaction)."""
    chaos = webhook_chaos(account_profile(session, account_id), stable_key)
    event_id = f"evt_{new_uuid().hex}"
    payload = canonical_json_bytes(
        {
            "effect": json.loads(effect_body),
            "eventId": event_id,
            "eventType": "effect.updated",
            "occurredAt": now.isoformat(),
        }
    )
    event = ProviderWebhookOutbox(
        provider_account_id=account_id,
        provider_effect_id=effect_id,
        event_id=event_id,
        payload_bytes=payload,
        status="DROPPED" if chaos.dropped else "PENDING",
        copies_remaining=0 if chaos.dropped else chaos.copies,
        attempt_count=0,
        next_attempt_at=now + timedelta(milliseconds=chaos.delay_ms),
    )
    session.add(event)
    return event


def http_webhook_transport(client: httpx.Client) -> WebhookTransport:
    def send(url: str, headers: dict[str, str], body: bytes) -> int:
        try:
            return client.post(url, content=body, headers=headers).status_code
        except httpx.HTTPError:
            return 0

    return send


def push_webhook_batch(
    factory: sessionmaker[Session],
    *,
    url: str,
    secret: str,
    transport: WebhookTransport,
    batch_size: int = 50,
    now: datetime | None = None,
) -> int:
    """Send every due event once; returns how many were sent."""
    moment = now or datetime.now(UTC)
    with factory() as session, session.begin():
        events = list(
            session.scalars(
                select(ProviderWebhookOutbox)
                .where(
                    ProviderWebhookOutbox.status == "PENDING",
                    ProviderWebhookOutbox.next_attempt_at <= moment,
                )
                .order_by(ProviderWebhookOutbox.next_attempt_at, ProviderWebhookOutbox.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for event in events:
            timestamp = str(int(moment.timestamp()))
            headers = {
                "Content-Type": "application/json",
                "X-Provider-Event-Id": event.event_id,
                "X-Provider-Timestamp": timestamp,
                "X-Provider-Signature": sign_webhook(event.payload_bytes, timestamp, secret),
            }
            status = transport(url, headers, event.payload_bytes)
            event.last_http_status = status or None
            if 200 <= status < 300:
                event.copies_remaining -= 1
                if event.copies_remaining == 0:
                    event.status = "DELIVERED"
                    event.delivered_at = moment
                continue
            event.attempt_count += 1
            if event.attempt_count >= MAX_PUSH_ATTEMPTS:
                event.status = "DEAD"
            else:
                delay = min(2**event.attempt_count, RETRY_CAP_SECONDS)
                event.next_attempt_at = moment + timedelta(seconds=delay)
        return len(events)
