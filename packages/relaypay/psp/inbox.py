"""Accept inbound PSP webhooks into the raw ``psp_events`` inbox.

Order matters: the signature and replay window are verified over the exact bytes *before* the
body is parsed, so unauthenticated input is never interpreted. The event is then matched to the
operation it names (by stable key) and stored once per ``(provider, provider_event_id)``. A
redelivery of the same bytes is a replay; the same id with different bytes is a conflict.
"""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.ids import new_public_id, new_uuid
from relaypay.provider_operations.models import ProviderOperation
from relaypay.psp.models import PspEvent

SIMULATOR = "SIMULATOR"
EVENT_ID_HEADER = "x-provider-event-id"
TIMESTAMP_HEADER = "x-provider-timestamp"
SIGNATURE_HEADER = "x-provider-signature"


class SimulatorEffect(BaseModel):
    model_config = ConfigDict(extra="allow")

    stableKey: str


class SimulatorEnvelope(BaseModel):
    """The simulator's webhook body: one effect update per event."""

    model_config = ConfigDict(extra="forbid", strict=True)

    eventId: str
    eventType: Literal["effect.updated"]
    occurredAt: str
    effect: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VerifiedEnvelope:
    event_id: str
    event_type: str
    stable_key: str
    signed_at: datetime


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.lower() == name), None)


def _unauthorized(code: str, message: str) -> RelayPayError:
    return RelayPayError(code=code, message=message, http_status=401)


def verify_simulator_webhook(
    headers: Mapping[str, str],
    body: bytes,
    *,
    secret: str,
    replay_seconds: int,
    now: datetime,
) -> VerifiedEnvelope:
    """Authenticate ``"<timestamp>." + body`` (HMAC-SHA256), then parse the envelope."""
    event_id = _header(headers, EVENT_ID_HEADER)
    timestamp_text = _header(headers, TIMESTAMP_HEADER)
    signature = _header(headers, SIGNATURE_HEADER)
    if not event_id or not timestamp_text or not signature:
        raise _unauthorized("PSP_WEBHOOK_HEADERS_MISSING", "Webhook authentication headers missing")
    try:
        signed_at = datetime.fromtimestamp(int(timestamp_text), tz=UTC)
    except (ValueError, OverflowError) as error:
        raise _unauthorized("INVALID_WEBHOOK_TIMESTAMP", "Webhook timestamp is invalid") from error
    if abs((now - signed_at).total_seconds()) > replay_seconds:
        raise _unauthorized(
            "WEBHOOK_REPLAY_WINDOW_EXCEEDED", "Webhook timestamp is outside the replay window"
        )
    expected = hmac.new(secret.encode(), timestamp_text.encode() + b"." + body, hashlib.sha256)
    if not hmac.compare_digest(signature.removeprefix("v1="), expected.hexdigest()):
        raise _unauthorized("INVALID_WEBHOOK_SIGNATURE", "Webhook signature is invalid")
    try:
        envelope = SimulatorEnvelope.model_validate_json(body)
        effect = SimulatorEffect.model_validate(envelope.effect)
    except ValidationError as error:
        raise RelayPayError(
            code="INVALID_PSP_EVENT", message="Webhook body is not a valid event", http_status=400
        ) from error
    if envelope.eventId != event_id:
        raise RelayPayError(
            code="PSP_EVENT_ID_MISMATCH",
            message="Webhook body id does not match its header",
            http_status=400,
        )
    return VerifiedEnvelope(event_id, envelope.eventType, effect.stableKey, signed_at)


def _matching_operation(session: Session, stable_key: str) -> ProviderOperation | None:
    operations = list(
        session.scalars(
            select(ProviderOperation).where(ProviderOperation.stable_provider_key == stable_key)
        )
    )
    # Stable keys embed globally unique public ids; more than one match is never trusted.
    return operations[0] if len(operations) == 1 else None


def accept_psp_event(
    factory: sessionmaker[Session],
    *,
    provider: str,
    headers: Mapping[str, str],
    body: bytes,
    webhook_secret: str,
    replay_seconds: int,
    now: datetime | None = None,
) -> tuple[PspEvent, bool]:
    """Verify, match and store one PSP webhook; returns ``(event, replayed)``."""
    if provider != SIMULATOR:
        raise RelayPayError(
            code="PSP_PROVIDER_UNSUPPORTED",
            message="No webhook adapter is configured for this provider",
            http_status=404,
        )
    verified = verify_simulator_webhook(
        headers,
        body,
        secret=webhook_secret,
        replay_seconds=replay_seconds,
        now=now or datetime.now(UTC),
    )
    digest = hashlib.sha256(body).digest()
    with factory() as session, session.begin():
        existing = session.scalar(
            select(PspEvent).where(
                PspEvent.provider == provider,
                PspEvent.provider_event_id == verified.event_id,
            )
        )
        if existing is not None:
            if existing.payload_sha256 != digest:
                raise RelayPayError(
                    code="PSP_EVENT_CONFLICT",
                    message="Provider event identifier is bound to different bytes",
                    http_status=409,
                )
            session.expunge(existing)
            return existing, True
        operation = _matching_operation(session, verified.stable_key)
        event = PspEvent(
            id=new_uuid(),
            public_id=new_public_id("pev"),
            provider=provider,
            provider_event_id=verified.event_id,
            event_type=verified.event_type,
            stable_key=verified.stable_key,
            signature_timestamp=verified.signed_at,
            payload_bytes=body,
            payload_sha256=digest,
            organisation_id=operation.organisation_id if operation else None,
            environment_id=operation.environment_id if operation else None,
            provider_operation_id=operation.id if operation else None,
            status="PENDING" if operation else "UNMATCHED",
            attempt_count=0,
        )
        session.add(event)
        try:
            session.flush()
        except IntegrityError:
            # A concurrent delivery of the same event won the unique race: re-read it.
            session.rollback()
            return accept_psp_event(
                factory,
                provider=provider,
                headers=headers,
                body=body,
                webhook_secret=webhook_secret,
                replay_seconds=replay_seconds,
                now=now,
            )
        session.expunge(event)
        return event, False
