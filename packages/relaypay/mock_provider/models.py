import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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
        CheckConstraint("operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')"),
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
            "'MISMATCHED', 'PENDING')"
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
