import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class MerchantAccount(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "merchant_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "reference"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        Index(
            "uq_merchant_accounts_default",
            "organisation_id",
            "environment_id",
            unique=True,
            postgresql_where=text("is_default AND status = 'ACTIVE'"),
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class SettlementRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "settlement_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
        CheckConstraint("status IN ('PROCESSING', 'COMPLETED', 'FAILED')"),
        CheckConstraint("settled_amount >= 0"),
        CheckConstraint("octet_length(idempotency_key_digest) = 32"),
        CheckConstraint("octet_length(fingerprint_sha256) = 32"),
        CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL "
            "AND response_bytes IS NOT NULL AND response_sha256 IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL "
            "AND response_bytes IS NULL AND response_sha256 IS NULL)"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    idempotency_key_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROCESSING")
    settled_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettlementItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "settlement_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "settlement_run_id"],
            [
                "settlement_runs.organisation_id",
                "settlement_runs.environment_id",
                "settlement_runs.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "capture_id"],
            ["captures.organisation_id", "captures.environment_id", "captures.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("capture_id"),
        UniqueConstraint("journal_id"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    settlement_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    capture_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    journal_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")


class BalanceTransaction(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "balance_transactions"
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
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("journal_id"),
        CheckConstraint("transaction_type IN ('OPENING', 'CAPTURE', 'REFUND', 'SETTLEMENT')"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint(
            "pending_delta <> 0 OR available_delta <> 0 OR receivable_delta <> 0 "
            "OR payout_clearing_delta <> 0"
        ),
        Index(
            "ix_balance_transactions_account_created",
            "merchant_account_id",
            "created_at",
            "id",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    journal_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(16), nullable=False)
    pending_delta: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    available_delta: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    receivable_delta: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payout_clearing_delta: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
