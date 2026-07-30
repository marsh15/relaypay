import hashlib
import hmac
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.connectors.crypto import encrypt_credential
from relaypay.connectors.models import (
    Connector,
    ConnectorCredentialVersion,
    ConnectorHealthObservation,
    ConnectorVersion,
    InboundWebhookAttempt,
    InboundWebhookEvent,
)
from relaypay.connectors.protocols import ConnectorAdapter, ConnectorError
from relaypay.errors import RelayPayError, not_found
from relaypay.identity.models import Environment
from relaypay.identity.security import Principal
from relaypay.identity.service import append_audit, require_organisation_admin
from relaypay.ids import new_public_id, new_uuid


@dataclass(frozen=True, slots=True)
class IssuedConnectorVersion:
    connector_public_id: str
    version_public_id: str
    version: int
    credential: str


@dataclass(frozen=True, slots=True)
class InboundClaim:
    event_id: uuid.UUID
    lease_token: uuid.UUID
    payload: bytes
    provider_event_id: str


def _environment(session: Session, principal: Principal, public_id: str) -> Environment:
    environment = session.scalar(
        select(Environment).where(
            Environment.organisation_id == principal.organisation_id,
            Environment.public_id == public_id,
            Environment.status == "ACTIVE",
        )
    )
    if environment is None:
        raise not_found("Environment")
    return environment


def create_connector_version(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    reference: str,
    kind: str,
    base_url: str,
    capabilities: list[str],
    timeout_ms: int,
    encryption_key: str,
    credential_name: str = "api_secret",
    credential: str | None = None,
) -> IssuedConnectorVersion:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    connector = session.scalar(
        select(Connector)
        .where(
            Connector.organisation_id == principal.organisation_id,
            Connector.environment_id == environment.id,
            Connector.reference == reference,
        )
        .with_for_update()
    )
    if connector is None:
        connector = Connector(
            id=new_uuid(),
            public_id=new_public_id("con"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            reference=reference,
            kind=kind,
            status="ACTIVE",
            circuit_state="CLOSED",
            consecutive_failures=0,
        )
        session.add(connector)
        session.flush()
    elif connector.kind != kind:
        raise RelayPayError(
            code="CONNECTOR_REFERENCE_CONFLICT",
            message="Connector reference is bound to a different kind",
            http_status=409,
        )
    version_number = (
        int(
            session.scalar(
                select(func.coalesce(func.max(ConnectorVersion.version), 0)).where(
                    ConnectorVersion.connector_id == connector.id
                )
            )
            or 0
        )
        + 1
    )
    plaintext = credential or secrets.token_urlsafe(32)
    version = ConnectorVersion(
        id=new_uuid(),
        public_id=new_public_id("cnv"),
        organisation_id=connector.organisation_id,
        environment_id=connector.environment_id,
        connector_id=connector.id,
        version=version_number,
        status="PENDING",
        base_url=base_url,
        capabilities=",".join(sorted(set(capabilities))),
        timeout_ms=timeout_ms,
    )
    session.add(version)
    session.flush()
    session.add(
        ConnectorCredentialVersion(
            id=new_uuid(),
            public_id=new_public_id("crd"),
            organisation_id=connector.organisation_id,
            environment_id=connector.environment_id,
            connector_version_id=version.id,
            credential_name=credential_name,
            encrypted_secret=encrypt_credential(plaintext, encryption_key),
            secret_sha256=hashlib.sha256(plaintext.encode()).digest(),
            key_version=1,
        )
    )
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="CONNECTOR_VERSION_CREATED",
        target_type="CONNECTOR",
        target_id=connector.public_id,
        details={"version": version_number, "kind": kind},
    )
    return IssuedConnectorVersion(connector.public_id, version.public_id, version_number, plaintext)


def verify_connector_version(
    factory: sessionmaker[Session],
    *,
    principal: Principal,
    environment_public_id: str,
    version_public_id: str,
    adapter: ConnectorAdapter,
) -> None:
    require_organisation_admin(principal)
    started = time.monotonic()
    observation = adapter.health()
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    error = adapter.classify(observation)
    valid = error is None and adapter.validate(observation)
    with factory() as session, session.begin():
        environment = _environment(session, principal, environment_public_id)
        version = session.scalar(
            select(ConnectorVersion)
            .where(
                ConnectorVersion.organisation_id == principal.organisation_id,
                ConnectorVersion.environment_id == environment.id,
                ConnectorVersion.public_id == version_public_id,
            )
            .with_for_update()
        )
        if version is None:
            raise not_found("Connector version")
        if version.status != "PENDING":
            raise RelayPayError(
                code="CONNECTOR_VERSION_NOT_PENDING",
                message="Only a pending connector version can be verified",
                http_status=409,
            )
        session.add(
            ConnectorHealthObservation(
                organisation_id=version.organisation_id,
                environment_id=version.environment_id,
                connector_id=version.connector_id,
                status="HEALTHY" if valid else "UNAVAILABLE",
                latency_ms=latency_ms,
                error_category=error.category if error else None,
                safe_error_code=error.code if error else None,
                provider_rate_limit_remaining=_header_int(
                    observation.headers, "x-ratelimit-remaining"
                ),
                provider_retry_after_seconds=_header_int(observation.headers, "retry-after"),
            )
        )
        if not valid:
            raise RelayPayError(
                code="CONNECTOR_VERIFICATION_FAILED",
                message="Connector verification did not return trusted healthy evidence",
                http_status=409,
            )
        version.verified_at = datetime.now(UTC)
        append_audit(
            session,
            principal=principal,
            environment_id=environment.id,
            action="CONNECTOR_VERSION_VERIFIED",
            target_type="CONNECTOR_VERSION",
            target_id=version.public_id,
            details={},
        )


def activate_connector_version(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    version_public_id: str,
) -> None:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    version = session.scalar(
        select(ConnectorVersion)
        .where(
            ConnectorVersion.organisation_id == principal.organisation_id,
            ConnectorVersion.environment_id == environment.id,
            ConnectorVersion.public_id == version_public_id,
        )
        .with_for_update()
    )
    if version is None:
        raise not_found("Connector version")
    if version.status != "PENDING" or version.verified_at is None:
        raise RelayPayError(
            code="CONNECTOR_VERSION_NOT_VERIFIED",
            message="A pending connector version must be verified before activation",
            http_status=409,
        )
    now = datetime.now(UTC)
    current = session.scalar(
        select(ConnectorVersion)
        .where(
            ConnectorVersion.connector_id == version.connector_id,
            ConnectorVersion.status == "ACTIVE",
        )
        .with_for_update()
    )
    if current is not None:
        current.status = "REVOKED"
        current.revoked_at = now
    version.status = "ACTIVE"
    version.activated_at = now
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="CONNECTOR_VERSION_ACTIVATED",
        target_type="CONNECTOR_VERSION",
        target_id=version.public_id,
        details={"version": version.version},
    )


def record_connector_result(
    session: Session,
    *,
    connector: Connector,
    latency_ms: int,
    error: ConnectorError | None,
    failure_threshold: int = 3,
) -> None:
    now = datetime.now(UTC)
    if error is None:
        connector.consecutive_failures = 0
        connector.circuit_state = "CLOSED"
        connector.opened_at = None
    else:
        connector.consecutive_failures += 1
        if connector.consecutive_failures >= failure_threshold:
            connector.circuit_state = "OPEN"
            connector.opened_at = now
    session.add(
        ConnectorHealthObservation(
            organisation_id=connector.organisation_id,
            environment_id=connector.environment_id,
            connector_id=connector.id,
            status="HEALTHY" if error is None else "DEGRADED",
            latency_ms=latency_ms,
            error_category=error.category if error else None,
            safe_error_code=error.code if error else None,
            provider_retry_after_seconds=error.retry_after_seconds if error else None,
        )
    )


def accept_inbound_webhook(
    factory: sessionmaker[Session],
    *,
    connector_public_id: str,
    provider_event_id: str,
    timestamp_text: str,
    signature: str,
    body: bytes,
    secret: str,
    replay_seconds: int,
    now: datetime | None = None,
) -> tuple[InboundWebhookEvent, bool]:
    resolved_now = now or datetime.now(UTC)
    try:
        timestamp = datetime.fromtimestamp(int(timestamp_text), tz=UTC)
    except (ValueError, OverflowError) as exc:
        raise RelayPayError(
            code="INVALID_WEBHOOK_TIMESTAMP",
            message="Webhook timestamp is invalid",
            http_status=401,
        ) from exc
    if abs((resolved_now - timestamp).total_seconds()) > replay_seconds:
        raise RelayPayError(
            code="WEBHOOK_REPLAY_WINDOW_EXCEEDED",
            message="Webhook timestamp is outside the replay window",
            http_status=401,
        )
    expected = hmac.new(secret.encode(), timestamp_text.encode() + b"." + body, hashlib.sha256)
    if not hmac.compare_digest(signature.removeprefix("v1="), expected.hexdigest()):
        raise RelayPayError(
            code="INVALID_WEBHOOK_SIGNATURE",
            message="Webhook signature is invalid",
            http_status=401,
        )
    digest = hashlib.sha256(body).digest()
    with factory() as session, session.begin():
        connector = session.scalar(
            select(Connector).where(
                Connector.public_id == connector_public_id,
                Connector.status == "ACTIVE",
            )
        )
        if connector is None:
            raise not_found("Connector")
        existing = session.scalar(
            select(InboundWebhookEvent).where(
                InboundWebhookEvent.connector_id == connector.id,
                InboundWebhookEvent.provider_event_id == provider_event_id,
            )
        )
        if existing is not None:
            if existing.payload_sha256 != digest:
                raise RelayPayError(
                    code="WEBHOOK_EVENT_CONFLICT",
                    message="Provider event identifier is bound to different bytes",
                    http_status=409,
                )
            return existing, True
        event = InboundWebhookEvent(
            id=new_uuid(),
            public_id=new_public_id("iwe"),
            organisation_id=connector.organisation_id,
            environment_id=connector.environment_id,
            connector_id=connector.id,
            provider_event_id=provider_event_id,
            signature_timestamp=timestamp,
            payload_bytes=body,
            payload_sha256=digest,
            status="PENDING",
            attempt_count=0,
        )
        session.add(event)
        return event, False


def claim_inbound_webhook(
    factory: sessionmaker[Session], *, lease_seconds: int = 30
) -> InboundClaim | None:
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        event = session.scalar(
            select(InboundWebhookEvent)
            .where(
                or_(
                    InboundWebhookEvent.status == "PENDING",
                    (
                        (InboundWebhookEvent.status == "PROCESSING")
                        & (InboundWebhookEvent.lease_expires_at <= now)
                    ),
                ),
                or_(
                    InboundWebhookEvent.next_attempt_at.is_(None),
                    InboundWebhookEvent.next_attempt_at <= now,
                ),
            )
            .order_by(InboundWebhookEvent.created_at, InboundWebhookEvent.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if event is None:
            return None
        token = new_uuid()
        event.status = "PROCESSING"
        event.lease_token = token
        event.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return InboundClaim(event.id, token, event.payload_bytes, event.provider_event_id)


def process_inbound_claim(
    factory: sessionmaker[Session],
    claim: InboundClaim,
    *,
    handler: Callable[[bytes, str], None],
    max_attempts: int = 3,
) -> bool:
    started = time.monotonic()
    error_code: str | None = None
    try:
        handler(claim.payload, claim.provider_event_id)
    except Exception:
        error_code = "INBOUND_PROCESSING_FAILED"
    elapsed = max(0, int((time.monotonic() - started) * 1000))
    with factory() as session, session.begin():
        event = session.scalar(
            select(InboundWebhookEvent)
            .where(
                InboundWebhookEvent.id == claim.event_id,
                InboundWebhookEvent.lease_token == claim.lease_token,
                InboundWebhookEvent.status == "PROCESSING",
            )
            .with_for_update()
        )
        if event is None:
            return False
        event.attempt_count += 1
        terminal = error_code is None or event.attempt_count >= max_attempts
        outcome = "PROCESSED" if error_code is None else "DEAD_LETTER" if terminal else "RETRY"
        session.add(
            InboundWebhookAttempt(
                organisation_id=event.organisation_id,
                environment_id=event.environment_id,
                inbound_webhook_event_id=event.id,
                attempt_number=event.attempt_count,
                outcome=outcome,
                safe_error_code=error_code,
                processing_ms=elapsed,
            )
        )
        event.lease_token = None
        event.lease_expires_at = None
        if error_code is None:
            event.status = "PROCESSED"
            event.processed_at = datetime.now(UTC)
        elif terminal:
            event.status = "DEAD_LETTER"
            event.dead_lettered_at = datetime.now(UTC)
        else:
            event.status = "PENDING"
            event.next_attempt_at = datetime.now(UTC) + timedelta(
                seconds=min(60, 2**event.attempt_count)
            )
        return error_code is None


def _header_int(headers: dict[str, str], name: str) -> int | None:
    value = next((value for key, value in headers.items() if key.lower() == name), None)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
