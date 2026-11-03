import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from kafka import KafkaProducer  # type: ignore[import-untyped]
from sqlalchemy import select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.agent_runtime.models import BusinessEventOutbox, ConsumedBusinessEvent
from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_public_id, new_uuid


class EventPublisher(Protocol):
    def publish(self, *, topic: str, key: bytes, value: bytes) -> None: ...


class RedpandaPublisher:
    def __init__(self, brokers: str) -> None:
        # kafka-python-ng 2.2.3 has no enable_idempotence producer config;
        # acks="all" plus the PostgreSQL outbox (publish_one marks rows
        # published only after an acknowledged send, and consumers dedupe on
        # event id) carries the correctness guarantees.
        self._producer = KafkaProducer(
            bootstrap_servers=[item.strip() for item in brokers.split(",")],
            acks="all",
            retries=5,
        )

    def publish(self, *, topic: str, key: bytes, value: bytes) -> None:
        self._producer.send(topic, key=key, value=value).get(timeout=10)

    def close(self) -> None:
        self._producer.close(timeout=5)


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    row_id: uuid.UUID
    lease_token: uuid.UUID
    topic: str
    key: bytes
    value: bytes


def append_business_event(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    organisation_public_id: str,
    environment_id: uuid.UUID,
    environment_public_id: str,
    event_type: str,
    resource_type: str,
    resource_id: str,
    payload: dict[str, object],
    now: datetime | None = None,
) -> BusinessEventOutbox:
    occurred_at = now or datetime.now(UTC)
    event_id = new_public_id("bev")
    payload_bytes = canonical_json_bytes(payload)
    envelope = {
        "eventId": event_id,
        "eventType": event_type,
        "schemaVersion": 1,
        "occurredAt": occurred_at.isoformat(),
        "organisationId": organisation_public_id,
        "environmentId": environment_public_id,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "payload": payload,
        "payloadSha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    row = BusinessEventOutbox(
        id=new_uuid(),
        organisation_id=organisation_id,
        environment_id=environment_id,
        event_id=event_id,
        event_type=event_type,
        schema_version=1,
        occurred_at=occurred_at,
        resource_type=resource_type,
        resource_id=resource_id,
        topic=f"relaypay.{event_type}.v1",
        partition_key=f"{organisation_public_id}:{environment_public_id}:{resource_id}",
        payload=payload,
        payload_sha256=hashlib.sha256(payload_bytes).digest(),
        event_bytes=canonical_json_bytes(envelope),
        publish_attempts=0,
        next_attempt_at=occurred_at,
    )
    session.add(row)
    return row


def _backoff(attempt: int, event_id: str) -> timedelta:
    base = min(300, 2 ** min(attempt, 8))
    jitter = int(hashlib.sha256(f"{event_id}:{attempt}".encode()).hexdigest()[:2], 16) % 7
    return timedelta(seconds=base + jitter)


def claim_event(
    session: Session,
    *,
    now: datetime,
    lease_seconds: int = 30,
    organisation_id: uuid.UUID | None = None,
) -> ClaimedEvent | None:
    row = session.scalar(
        select(BusinessEventOutbox)
        .where(
            BusinessEventOutbox.published_at.is_(None),
            BusinessEventOutbox.organisation_id == organisation_id
            if organisation_id is not None
            else true(),
            BusinessEventOutbox.next_attempt_at <= now,
            (BusinessEventOutbox.lease_expires_at.is_(None))
            | (BusinessEventOutbox.lease_expires_at <= now),
        )
        .order_by(BusinessEventOutbox.created_at, BusinessEventOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is None:
        return None
    token = new_uuid()
    row.lease_token = token
    row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    row.publish_attempts += 1
    return ClaimedEvent(row.id, token, row.topic, row.partition_key.encode(), row.event_bytes)


def publish_one(
    factory: sessionmaker[Session],
    publisher: EventPublisher,
    *,
    now: datetime,
    organisation_id: uuid.UUID | None = None,
) -> bool:
    with factory() as session, session.begin():
        claim = claim_event(session, now=now, organisation_id=organisation_id)
    if claim is None:
        return False
    try:
        publisher.publish(topic=claim.topic, key=claim.key, value=claim.value)
    except Exception:
        with factory() as session, session.begin():
            row = session.get(BusinessEventOutbox, claim.row_id)
            if row is not None and row.lease_token == claim.lease_token:
                row.next_attempt_at = now + _backoff(row.publish_attempts, row.event_id)
                row.lease_token = None
                row.lease_expires_at = None
        raise
    with factory() as session, session.begin():
        row = session.get(BusinessEventOutbox, claim.row_id)
        if row is not None and row.lease_token == claim.lease_token:
            row.published_at = now
            row.lease_token = None
            row.lease_expires_at = None
    return True


def _consumption_marker(
    session: Session, *, consumer_name: str, event_id: str
) -> ConsumedBusinessEvent | None:
    return session.scalar(
        select(ConsumedBusinessEvent).where(
            ConsumedBusinessEvent.consumer_name == consumer_name,
            ConsumedBusinessEvent.event_id == event_id,
        )
    )


def _assert_identical_replay(
    marker: ConsumedBusinessEvent,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    payload_sha256: bytes,
) -> None:
    # The marker binds the event id to one scope and one payload. Anything else is a
    # different request reusing the id, so it gets a generic replay conflict.
    if (
        marker.organisation_id != organisation_id
        or marker.environment_id != environment_id
        or marker.payload_sha256 != payload_sha256
    ):
        raise RelayPayError(
            code="EVENT_REPLAY_CONFLICT",
            message="The event id is already bound to a different payload",
            http_status=409,
        )


def record_consumption(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    consumer_name: str,
    event_id: str,
    payload_sha256: bytes,
) -> bool:
    """Record one consumer effect per event id.

    Returns True for the first consumption and False for an identical replay. A marker
    bound to another scope or payload raises EVENT_REPLAY_CONFLICT (409) instead.
    """
    marker = _consumption_marker(session, consumer_name=consumer_name, event_id=event_id)
    if marker is not None:
        _assert_identical_replay(
            marker,
            organisation_id=organisation_id,
            environment_id=environment_id,
            payload_sha256=payload_sha256,
        )
        return False
    nested = session.begin_nested()
    try:
        session.add(
            ConsumedBusinessEvent(
                id=new_uuid(),
                organisation_id=organisation_id,
                environment_id=environment_id,
                consumer_name=consumer_name,
                event_id=event_id,
                payload_sha256=payload_sha256,
            )
        )
        session.flush()
        nested.commit()
        return True
    except IntegrityError as exc:
        nested.rollback()
        # Only the consumer/event unique constraint represents a concurrent replay.
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != (
            "consumed_business_events_consumer_name_event_id_key"
        ):
            raise
        concurrent = _consumption_marker(session, consumer_name=consumer_name, event_id=event_id)
        if concurrent is None:
            raise
        _assert_identical_replay(
            concurrent,
            organisation_id=organisation_id,
            environment_id=environment_id,
            payload_sha256=payload_sha256,
        )
        return False
