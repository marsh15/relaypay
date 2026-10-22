import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.ids import new_uuid
from relaypay.mock_provider.database import ProviderBase


class ProviderAccount(ProviderBase):
    __tablename__ = "provider_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    signing_secret_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderEffect(ProviderBase):
    __tablename__ = "provider_effects"
    __table_args__ = (
        UniqueConstraint("provider_account_id", "stable_key"),
        CheckConstraint("operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND', 'VOID')"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("outcome IN ('PENDING', 'SUCCEEDED', 'DECLINED')"),
        CheckConstraint(
            "(outcome = 'DECLINED' AND decline_code IS NOT NULL) OR "
            "(outcome <> 'DECLINED' AND decline_code IS NULL)"
        ),
        CheckConstraint(
            "(outcome = 'PENDING' AND completed_at IS NULL AND response_bytes IS NULL) OR "
            "(outcome IN ('SUCCEEDED', 'DECLINED') AND completed_at IS NOT NULL "
            "AND response_bytes IS NOT NULL)"
        ),
        Index("ix_provider_effects_stable_key", "provider_account_id", "stable_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    provider_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    decline_code: Mapped[str | None] = mapped_column(String(64))
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderFaultDirective(ProviderBase):
    __tablename__ = "provider_fault_directives"
    __table_args__ = (
        UniqueConstraint("provider_account_id", "stable_key", "fault_type"),
        CheckConstraint("remaining_uses >= 0"),
        CheckConstraint(
            "fault_type IN ('LOSE_RESPONSE', 'DECLINE', 'MALFORMED', 'UNSIGNED', "
            "'MISMATCHED', 'PENDING', 'SLOW_RESPONSE')"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    provider_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fault_type: Mapped[str] = mapped_column(String(24), nullable=False)
    remaining_uses: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderStatementExport(ProviderBase):
    __tablename__ = "provider_statement_exports"
    __table_args__ = (
        UniqueConstraint("provider_account_id", "source_reference"),
        CheckConstraint("source_format IN ('CSV', 'JSON')"),
        CheckConstraint("period_end > period_start"),
        CheckConstraint("octet_length(raw_bytes) BETWEEN 1 AND 1048576"),
        CheckConstraint("octet_length(raw_sha256) = 32"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False
    )
    source_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    source_format: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    raw_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderStatementItem(ProviderBase):
    __tablename__ = "provider_statement_items"
    __table_args__ = (
        UniqueConstraint("statement_export_id", "ordinal"),
        UniqueConstraint("statement_export_id", "provider_item_id"),
        CheckConstraint("ordinal > 0"),
        CheckConstraint("operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND', 'VOID')"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("provider_status IN ('PENDING', 'SUCCEEDED', 'DECLINED')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    statement_export_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_statement_exports.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderChaosProfile(ProviderBase):
    """Seeded probabilistic faults for one account (M17). Each decision is a pure function of
    ``(seed, stable key, aspect)``, so a chaos run replays exactly from its seed."""

    __tablename__ = "provider_chaos_profiles"
    __table_args__ = (
        UniqueConstraint("provider_account_id"),
        CheckConstraint(
            "error_before_bps BETWEEN 0 AND 10000 AND error_after_bps BETWEEN 0 AND 10000 "
            "AND lose_response_bps BETWEEN 0 AND 10000 AND slow_bps BETWEEN 0 AND 10000 "
            "AND pending_bps BETWEEN 0 AND 10000 AND decline_bps BETWEEN 0 AND 10000 "
            "AND webhook_drop_bps BETWEEN 0 AND 10000 "
            "AND webhook_duplicate_bps BETWEEN 0 AND 10000",
            name="provider_chaos_profiles_bps_check",
        ),
        CheckConstraint(
            "slow_ms BETWEEN 0 AND 10000 AND webhook_max_delay_ms BETWEEN 0 AND 600000",
            name="provider_chaos_profiles_timing_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    provider_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error_before_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_after_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lose_response_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slow_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    slow_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decline_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    webhook_drop_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    webhook_duplicate_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    webhook_max_delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderWebhookOutbox(ProviderBase):
    """One signed webhook event per effect, written in the effect's transaction (M17).

    ``copies_remaining`` > 1 models a PSP that delivers the same event more than once;
    ``DROPPED`` events are kept so a chaos run can show they never arrived.
    """

    __tablename__ = "provider_webhook_outbox"
    __table_args__ = (
        UniqueConstraint("event_id"),
        CheckConstraint(
            "status IN ('PENDING', 'DELIVERED', 'DROPPED', 'DEAD')",
            name="provider_webhook_outbox_status_check",
        ),
        CheckConstraint("copies_remaining >= 0", name="provider_webhook_outbox_copies_check"),
        CheckConstraint("attempt_count >= 0", name="provider_webhook_outbox_attempts_check"),
        Index("ix_provider_webhook_outbox_due", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    provider_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False
    )
    provider_effect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_effects.id"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    copies_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
