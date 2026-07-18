import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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
        UniqueConstraint("organisation_id", "merchant_customer_reference"),
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
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "merchant_reference"),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "merchant_reference"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        Index("ix_payment_intents_organisation_created", "organisation_id", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")


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
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
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
            ["organisation_id", "authorization_id"],
            ["authorizations.organisation_id", "authorizations.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "journal_id"], ["journals.organisation_id", "journals.id"]
        ),
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
            ["organisation_id", "capture_id"], ["captures.organisation_id", "captures.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organisation_id", "journal_id"], ["journals.organisation_id", "journals.id"]
        ),
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
