import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Customer(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "merchant_customer_reference"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    merchant_customer_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))


class PaymentIntent(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "payment_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "customer_id"], ["customers.organisation_id", "customers.id"]
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "merchant_reference"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        Index("ix_payment_intents_organisation_created", "organisation_id", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
