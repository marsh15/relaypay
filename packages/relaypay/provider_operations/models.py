import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class ProviderOperation(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "provider_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "stable_provider_key"),
        UniqueConstraint("organisation_id", "kind", "resource_id"),
        CheckConstraint("resource_type IN ('AUTHORIZATION', 'CAPTURE', 'REFUND')"),
        CheckConstraint("kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')"),
        CheckConstraint("status IN ('PROCESSING', 'SUCCEEDED', 'FAILED', 'REQUIRES_REVIEW')"),
        CheckConstraint("attempt_count >= 0"),
        CheckConstraint("apply_failure_count >= 0"),
        CheckConstraint(
            "last_sent_at IS NULL OR (provider_request_bytes IS NOT NULL "
            "AND provider_request_sha256 IS NOT NULL AND attempt_count >= 1)"
        ),
        CheckConstraint(
            "(status IN ('SUCCEEDED', 'FAILED') AND terminal_http_status IS NOT NULL "
            "AND terminal_response_bytes IS NOT NULL AND terminal_response_sha256 IS NOT NULL "
            "AND finalized_at IS NOT NULL) OR "
            "(status IN ('PROCESSING', 'REQUIRES_REVIEW') AND terminal_http_status IS NULL "
            "AND terminal_response_bytes IS NULL AND terminal_response_sha256 IS NULL "
            "AND finalized_at IS NULL)"
        ),
        Index("ix_provider_operations_recovery", "status", "next_lookup_at"),
        Index("ix_provider_operations_lease_expiry", "lookup_lease_expires_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_type: Mapped[str] = mapped_column(String(24), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    stable_provider_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROCESSING")
    review_reason: Mapped[str | None] = mapped_column(String(64))
    provider_request_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    provider_request_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_lookup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lookup_lease_token: Mapped[uuid.UUID | None] = mapped_column()
    lookup_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    apply_failure_count: Mapped[int] = mapped_column(nullable=False, default=0)
    terminal_http_status: Mapped[int | None] = mapped_column(SmallInteger)
    terminal_response_headers: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    terminal_response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    terminal_response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "provider_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("provider_operation_id", "sequence"),
        CheckConstraint("sequence > 0"),
        CheckConstraint("attempt_kind IN ('MUTATION', 'LOOKUP')"),
        CheckConstraint(
            "state IN ('SENT', 'RESPONSE_RECEIVED', 'TRANSPORT_ERROR', 'VALIDATION_REJECTED')"
        ),
        Index(
            "uq_provider_attempts_one_mutation",
            "provider_operation_id",
            unique=True,
            postgresql_where=text("attempt_kind = 'MUTATION'"),
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    attempt_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_http_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    provider_signature_valid: Mapped[bool | None] = mapped_column()
    classification: Mapped[str | None] = mapped_column(String(64))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationHistory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "operation_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "evidence_attempt_id"],
            ["provider_attempts.organisation_id", "provider_attempts.id"],
        ),
        CheckConstraint(
            "actor_type IN ('REQUEST', 'RECOVERY_WORKER', 'ADMIN_LOOKUP', 'FINALIZER')"
        ),
        Index("ix_operation_history_operation_created", "provider_operation_id", "created_at"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_attempt_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)


class IdempotencyRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
        ),
        UniqueConstraint("organisation_id", "key_digest"),
        CheckConstraint("target_type IN ('PAYMENT_INTENT', 'PROVIDER_OPERATION')"),
        CheckConstraint(
            "(is_terminal AND http_status IS NOT NULL AND response_bytes IS NOT NULL "
            "AND response_sha256 IS NOT NULL AND finalized_at IS NOT NULL) OR "
            "(NOT is_terminal AND provider_operation_id IS NOT NULL AND http_status IS NULL "
            "AND response_bytes IS NULL AND response_sha256 IS NULL AND finalized_at IS NULL)"
        ),
        Index("ix_idempotency_operation_order", "provider_operation_id", "id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    key_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_hint: Mapped[str | None] = mapped_column(String(32))
    fingerprint_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    is_terminal: Mapped[bool] = mapped_column(nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_headers: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
