import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class LedgerAccount(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "code", "currency"),
        CheckConstraint("account_type IN ('ASSET', 'LIABILITY')"),
        CheckConstraint("currency = 'INR'"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")


class Journal(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "journals"
    __table_args__ = (
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("provider_operation_id"),
        UniqueConstraint("organisation_id", "journal_type", "reference_id"),
        CheckConstraint("journal_type IN ('CAPTURE', 'REFUND', 'COMPENSATION')"),
        CheckConstraint("currency = 'INR'"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    journal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Posting(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "postings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "journal_id"], ["journals.organisation_id", "journals.id"]
        ),
        ForeignKeyConstraint(
            ["organisation_id", "account_id"],
            ["ledger_accounts.organisation_id", "ledger_accounts.id"],
        ),
        CheckConstraint("side IN ('DEBIT', 'CREDIT')"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        Index("ix_postings_account_created", "organisation_id", "account_id", "created_at", "id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    journal_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
