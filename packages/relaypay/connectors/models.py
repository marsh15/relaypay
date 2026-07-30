import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Connector(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "connectors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "reference"),
        CheckConstraint("kind IN ('PAYMENT', 'BANK', 'COMMERCE')"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        CheckConstraint("circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN')"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    circuit_state: Mapped[str] = mapped_column(String(16), nullable=False, default="CLOSED")
    consecutive_failures: Mapped[int] = mapped_column(nullable=False, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "connector_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "connector_id"],
            ["connectors.organisation_id", "connectors.environment_id", "connectors.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("connector_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("status IN ('PENDING', 'ACTIVE', 'REVOKED')"),
        CheckConstraint("timeout_ms BETWEEN 100 AND 30000"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connector_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    capabilities: Mapped[str] = mapped_column(String(256), nullable=False)
    timeout_ms: Mapped[int] = mapped_column(nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectorCredentialVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "connector_credential_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "connector_version_id"],
            [
                "connector_versions.organisation_id",
                "connector_versions.environment_id",
                "connector_versions.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("connector_version_id", "credential_name"),
        CheckConstraint("octet_length(secret_sha256) = 32"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connector_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    credential_name: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(nullable=False, default=1)


class ConnectorHealthObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "connector_health_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "connector_id"],
            ["connectors.organisation_id", "connectors.environment_id", "connectors.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        CheckConstraint("status IN ('HEALTHY', 'DEGRADED', 'UNAVAILABLE')"),
        CheckConstraint(
            "error_category IS NULL OR "
            "error_category IN ('TEMPORARY', 'PERMANENT', 'AMBIGUOUS', 'RATE_LIMITED')"
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connector_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(16))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    provider_rate_limit_remaining: Mapped[int | None] = mapped_column()
    provider_retry_after_seconds: Mapped[int | None] = mapped_column()


class InboundWebhookEvent(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "inbound_webhook_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "connector_id"],
            ["connectors.organisation_id", "connectors.environment_id", "connectors.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("connector_id", "provider_event_id"),
        CheckConstraint("octet_length(payload_sha256) = 32"),
        CheckConstraint("status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'DEAD_LETTER')"),
        Index("ix_inbound_webhook_claim", "status", "next_attempt_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    connector_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    signature_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InboundWebhookAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "inbound_webhook_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "inbound_webhook_event_id"],
            [
                "inbound_webhook_events.organisation_id",
                "inbound_webhook_events.environment_id",
                "inbound_webhook_events.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("inbound_webhook_event_id", "attempt_number"),
        CheckConstraint("attempt_number > 0"),
        CheckConstraint("outcome IN ('PROCESSED', 'RETRY', 'DEAD_LETTER')"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    inbound_webhook_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    processing_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
