import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Beneficiary(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "beneficiaries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "reference"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        CheckConstraint("currency = 'INR'"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bank_account_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class Payout(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "payouts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "beneficiary_id"],
            [
                "beneficiaries.organisation_id",
                "beneficiaries.environment_id",
                "beneficiaries.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("status IN ('PROCESSING', 'REQUIRES_REVIEW', 'SUCCEEDED', 'FAILED')"),
        CheckConstraint("octet_length(idempotency_key_digest) = 32"),
        CheckConstraint("octet_length(fingerprint_sha256) = 32"),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND journal_id IS NOT NULL AND completed_at IS NOT NULL) OR "
            "(status <> 'SUCCEEDED' AND journal_id IS NULL)"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    beneficiary_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROCESSING")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    review_reason: Mapped[str | None] = mapped_column(String(64))
    journal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    idempotency_key_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_http_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayoutReservationHistory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "payout_reservation_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("payout_id", "sequence"),
        CheckConstraint("sequence > 0"),
        CheckConstraint("action IN ('RESERVED', 'RELEASED', 'CONSUMED')"),
        CheckConstraint(
            "(action = 'RESERVED' AND amount_delta > 0) OR "
            "(action IN ('RELEASED', 'CONSUMED') AND amount_delta < 0)"
        ),
        Index("ix_payout_reservation_account", "merchant_account_id", "created_at", "id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payout_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)


class PayoutAttempt(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "payout_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("payout_id", "attempt_number"),
        UniqueConstraint("organisation_id", "environment_id", "stable_provider_key"),
        UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
        CheckConstraint("attempt_number > 0"),
        CheckConstraint("status IN ('PREPARED', 'SENT', 'AMBIGUOUS', 'SUCCEEDED', 'FAILED')"),
        CheckConstraint(
            "last_sent_at IS NULL OR (request_bytes IS NOT NULL AND request_sha256 IS NOT NULL)"
        ),
        CheckConstraint(
            "(idempotency_key_digest IS NULL AND fingerprint_sha256 IS NULL) OR "
            "(octet_length(idempotency_key_digest) = 32 "
            "AND octet_length(fingerprint_sha256) = 32)"
        ),
        Index(
            "uq_payout_attempts_active",
            "payout_id",
            unique=True,
            postgresql_where=text("status IN ('PREPARED', 'SENT', 'AMBIGUOUS')"),
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payout_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_provider_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PREPARED")
    request_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    request_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    review_reason: Mapped[str | None] = mapped_column(String(64))
    idempotency_key_digest: Mapped[bytes | None] = mapped_column(LargeBinary)
    fingerprint_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)


class PayoutAttemptEvidence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "payout_attempt_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_attempt_id"],
            [
                "payout_attempts.organisation_id",
                "payout_attempts.environment_id",
                "payout_attempts.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("payout_attempt_id", "sequence"),
        CheckConstraint("sequence > 0"),
        CheckConstraint("evidence_kind IN ('MUTATION_SEND', 'MUTATION_RESULT', 'LOOKUP')"),
        CheckConstraint(
            "state IN ('SENT', 'RESPONSE_RECEIVED', 'TRANSPORT_ERROR', 'VALIDATION_REJECTED')"
        ),
        Index(
            "uq_payout_attempt_one_mutation",
            "payout_attempt_id",
            unique=True,
            postgresql_where=text("evidence_kind = 'MUTATION_SEND'"),
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payout_attempt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_http_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    bank_signature_valid: Mapped[bool | None] = mapped_column()
    classification: Mapped[str | None] = mapped_column(String(64))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayoutHistory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "payout_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_attempt_id"],
            [
                "payout_attempts.organisation_id",
                "payout_attempts.environment_id",
                "payout_attempts.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        CheckConstraint("actor_type IN ('ADMIN', 'PAYOUT_WORKER', 'RECOVERY_WORKER', 'FINALIZER')"),
        Index("ix_payout_history_payout_created", "payout_id", "created_at", "id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payout_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payout_attempt_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)


class PayoutEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "payout_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("payout_id", "event_type"),
        CheckConstraint("event_type = 'payout.succeeded.v1'"),
        CheckConstraint("octet_length(event_sha256) = 32"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payout_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
