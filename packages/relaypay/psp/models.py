import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import UpdatedAtMixin, UUIDPrimaryKeyMixin

PSP_PROVIDERS = ("SIMULATOR", "RAZORPAY", "CASHFREE")
PSP_EVENT_STATUSES = (
    "PENDING",  # verified, matched, waiting to be applied
    "PROCESSING",  # leased by the poller
    "APPLIED",  # recorded as WEBHOOK evidence and finalized the operation
    "IGNORED",  # duplicate or stale for the operation's state
    "DEFERRED",  # not applied (ordering decision unavailable); polling remains the guarantee
    "REVIEW",  # contradicts the operation's terminal state
    "UNMATCHED",  # names no operation RelayPay knows; kept for reconciliation
    "DEAD_LETTER",  # processing kept failing
)


class PspEvent(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """Raw inbox of signature-verified PSP webhook events (one row per provider event id).

    The raw bytes, digest and identity never change after insert (column-scoped immutability
    trigger). Only the processing lifecycle columns move.
    """

    __tablename__ = "psp_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id"),
        CheckConstraint(
            "provider IN ('SIMULATOR', 'RAZORPAY', 'CASHFREE')", name="psp_events_provider_check"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'APPLIED', 'IGNORED', 'DEFERRED', 'REVIEW', "
            "'UNMATCHED', 'DEAD_LETTER')",
            name="psp_events_status_check",
        ),
        CheckConstraint("octet_length(payload_sha256) = 32", name="psp_events_digest_check"),
        CheckConstraint(
            "(provider_operation_id IS NULL) = (status = 'UNMATCHED')",
            name="psp_events_match_check",
        ),
        CheckConstraint(
            "(organisation_id IS NULL) = (provider_operation_id IS NULL) "
            "AND (environment_id IS NULL) = (provider_operation_id IS NULL)",
            name="psp_events_tenant_check",
        ),
        Index("ix_psp_events_claim", "status", "next_attempt_at"),
        Index("ix_psp_events_operation", "provider_operation_id", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stable_key: Mapped[str | None] = mapped_column(String(128))
    signature_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column()
    environment_id: Mapped[uuid.UUID | None] = mapped_column()
    provider_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_operations.id")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome_code: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
