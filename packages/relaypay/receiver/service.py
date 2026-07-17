import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from relaypay.receiver.models import ReceivedEvent


class ReceiverValidationError(ValueError):
    pass


class ReceiverContradictionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    event_id: str
    duplicate: bool
    delivery_count: int


def receive_event(
    session: Session,
    *,
    body: bytes,
    event_id: str,
    timestamp_text: str,
    signature: str,
    secret: str,
    max_skew_seconds: int = 300,
) -> ReceiveResult:
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise ReceiverValidationError("invalid timestamp") from exc
    now = datetime.now(UTC)
    if abs(int(now.timestamp()) - timestamp) > max_skew_seconds:
        raise ReceiverValidationError("expired timestamp")
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if not signature.startswith("v1=") or not hmac.compare_digest(signature[3:], expected):
        raise ReceiverValidationError("invalid signature")
    try:
        payload: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiverValidationError("invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("id") != event_id:
        raise ReceiverValidationError("event identifier mismatch")
    digest = hashlib.sha256(body).digest()
    inserted = session.scalar(
        insert(ReceivedEvent)
        .values(
            event_id=event_id,
            event_sha256=digest,
            first_received_at=now,
            last_received_at=now,
            delivery_count=1,
            signature_timestamp=timestamp,
        )
        .on_conflict_do_nothing(index_elements=[ReceivedEvent.event_id])
        .returning(ReceivedEvent.event_id)
    )
    if inserted is not None:
        return ReceiveResult(event_id, False, 1)
    existing = session.scalar(
        select(ReceivedEvent).where(ReceivedEvent.event_id == event_id).with_for_update()
    )
    if existing is None:
        raise RuntimeError("receiver dedup row disappeared")
    if not hmac.compare_digest(existing.event_sha256, digest):
        raise ReceiverContradictionError("event bytes contradict the stored event")
    existing.delivery_count += 1
    existing.last_received_at = now
    return ReceiveResult(event_id, True, existing.delivery_count)
