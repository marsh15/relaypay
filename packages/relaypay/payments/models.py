import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Customer(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "merchant_customer_reference"),
        UniqueConstraint("organisation_id", "id"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_customer_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))


class PaymentIntent(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "payment_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "customer_id"], ["customers.organisation_id", "customers.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "customer_id"],
            ["customers.organisation_id", "customers.environment_id", "customers.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "merchant_reference"),
        UniqueConstraint("organisation_id", "id"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint(
            "status IN ('created', 'requires_action', 'authorised', 'captured', "
            "'partially_refunded', 'refunded', 'failed', 'expired', 'cancelled')",
            name="payment_intents_status_check",
        ),
        Index("ix_payment_intents_organisation_created", "organisation_id", "created_at"),
        Index(
            "ix_payment_intents_scope_status_created_id",
            "organisation_id",
            "environment_id",
            "status",
            text("created_at DESC"),
            "id",
        ),
        Index(
            "ix_payment_intents_scope_created_id",
            "organisation_id",
            "environment_id",
            "created_at",
            "id",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    # Written only by payments.transitions.transition(); a trigger enforces the transition table.
    # The Python-side default keeps hand-built rows (tests, fixtures) valid; service code sets it
    # through transitions.record_creation(), which also writes the initial history row.
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    status_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class PaymentStatusHistory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable record of every payment status change (trigger from migration 0001)."""

    __tablename__ = "payment_status_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payment_intent_id"],
            [
                "payment_intents.organisation_id",
                "payment_intents.environment_id",
                "payment_intents.id",
            ],
        ),
        UniqueConstraint("payment_intent_id", "status_version"),
        CheckConstraint("status_version >= 0"),
        CheckConstraint(
            "to_status IN ('created', 'requires_action', 'authorised', 'captured', "
            "'partially_refunded', 'refunded', 'failed', 'expired', 'cancelled')",
            name="payment_status_history_to_status_check",
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status IN ('created', 'requires_action', 'authorised', "
            "'captured', 'partially_refunded', 'refunded', 'failed', 'expired', 'cancelled')",
            name="payment_status_history_from_status_check",
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    status_version: Mapped[int] = mapped_column(Integer, nullable=False)


class Authorization(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "authorizations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payment_intent_id"],
            [
                "payment_intents.organisation_id",
                "payment_intents.environment_id",
                "payment_intents.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "payment_intent_id"),
        UniqueConstraint("provider_operation_id"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("status IN ('PROCESSING', 'SUCCEEDED', 'FAILED', 'REQUIRES_REVIEW')"),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND authorized_at IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'FAILED' AND authorized_at IS NULL AND failure_code IS NOT NULL) OR "
            "(status IN ('PROCESSING', 'REQUIRES_REVIEW') AND authorized_at IS NULL)"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROCESSING")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Capture(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "captures"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payment_intent_id"],
            [
                "payment_intents.organisation_id",
                "payment_intents.environment_id",
                "payment_intents.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "authorization_id"],
            ["authorizations.organisation_id", "authorizations.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "authorization_id"],
            [
                "authorizations.organisation_id",
                "authorizations.environment_id",
                "authorizations.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "journal_id"], ["journals.organisation_id", "journals.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "payment_intent_id"),
        UniqueConstraint("provider_operation_id"),
        UniqueConstraint("journal_id"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("status IN ('PROCESSING', 'SUCCEEDED', 'FAILED', 'REQUIRES_REVIEW')"),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND captured_at IS NOT NULL AND journal_id IS NOT NULL "
            "AND failure_code IS NULL) OR "
            "(status = 'FAILED' AND captured_at IS NULL AND journal_id IS NULL "
            "AND failure_code IS NOT NULL) OR "
            "(status IN ('PROCESSING', 'REQUIRES_REVIEW') AND captured_at IS NULL "
            "AND journal_id IS NULL)"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    authorization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    journal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROCESSING")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Refund(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "refunds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payment_intent_id"],
            [
                "payment_intents.organisation_id",
                "payment_intents.environment_id",
                "payment_intents.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "capture_id"], ["captures.organisation_id", "captures.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "capture_id"],
            ["captures.organisation_id", "captures.environment_id", "captures.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "journal_id"], ["journals.organisation_id", "journals.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("provider_operation_id"),
        UniqueConstraint("journal_id"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("status IN ('PROCESSING', 'SUCCEEDED', 'FAILED', 'REQUIRES_REVIEW')"),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND refunded_at IS NOT NULL AND journal_id IS NOT NULL "
            "AND failure_code IS NULL) OR "
            "(status = 'FAILED' AND refunded_at IS NULL AND journal_id IS NULL "
            "AND failure_code IS NOT NULL) OR "
            "(status IN ('PROCESSING', 'REQUIRES_REVIEW') AND refunded_at IS NULL "
            "AND journal_id IS NULL)"
        ),
        Index(
            "uq_refunds_merchant_reference",
            "organisation_id",
            "environment_id",
            "merchant_refund_reference",
            unique=True,
            postgresql_where=text("merchant_refund_reference IS NOT NULL"),
        ),
        Index("ix_refunds_payment_status", "organisation_id", "payment_intent_id", "status"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    capture_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    journal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    merchant_refund_reference: Mapped[str | None] = mapped_column(String(128))
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROCESSING")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
