import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from kafka import KafkaProducer  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.agent_runtime.models import BusinessEventOutbox, ConsumedBusinessEvent
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_public_id, new_uuid


class EventPublisher(Protocol):
    def publish(self, *, topic: str, key: bytes, value: bytes) -> None: ...


class RedpandaPublisher:
    def __init__(self, brokers: str) -> None:
        self._producer = KafkaProducer(
            bootstrap_servers=[item.strip() for item in brokers.split(",")],
            acks="all",
            enable_idempotence=True,
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


def claim_event(session: Session, *, now: datetime, lease_seconds: int = 30) -> ClaimedEvent | None:
    row = session.scalar(
        select(BusinessEventOutbox)
        .where(
            BusinessEventOutbox.published_at.is_(None),
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
    factory: sessionmaker[Session], publisher: EventPublisher, *, now: datetime
) -> bool:
    with factory() as session, session.begin():
        claim = claim_event(session, now=now)
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


def record_consumption(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    consumer_name: str,
    event_id: str,
    payload_sha256: bytes,
) -> bool:
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
    except IntegrityError:
        nested.rollback()
        return False
