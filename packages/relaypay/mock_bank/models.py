import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.ids import new_uuid
from relaypay.mock_bank.database import BankBase


class BankAccount(BankBase):
    __tablename__ = "bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    signing_secret_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BankEffect(BankBase):
    __tablename__ = "bank_effects"
    __table_args__ = (
        UniqueConstraint("bank_account_id", "stable_key"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("outcome IN ('PENDING', 'SUCCEEDED', 'DECLINED')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bank_accounts.id"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    beneficiary_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    payout_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    decline_code: Mapped[str | None] = mapped_column(String(64))
    response_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BankFaultDirective(BankBase):
    __tablename__ = "bank_fault_directives"
    __table_args__ = (
        UniqueConstraint("bank_account_id", "stable_key", "fault_type"),
        CheckConstraint("fault_type IN ('LOSE_RESPONSE', 'DECLINE', 'PENDING')"),
        CheckConstraint("remaining_uses >= 0"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bank_accounts.id"), nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fault_type: Mapped[str] = mapped_column(String(24), nullable=False)
    remaining_uses: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
