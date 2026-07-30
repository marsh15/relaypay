import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.ids import new_uuid
from relaypay.mock_commerce.database import CommerceBase


class CommerceAccount(CommerceBase):
    __tablename__ = "commerce_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    signing_secret_digest: Mapped[bytes] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommerceOrder(CommerceBase):
    __tablename__ = "commerce_orders"
    __table_args__ = (
        UniqueConstraint("commerce_account_id", "external_reference"),
        CheckConstraint("total_amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("status IN ('OPEN', 'PAID', 'REFUNDED')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    commerce_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commerce_accounts.id"), nullable=False
    )
    external_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CommercePaymentLink(CommerceBase):
    __tablename__ = "commerce_payment_links"
    __table_args__ = (
        UniqueConstraint("commerce_order_id", "relaypay_payment_id"),
        CheckConstraint("linked_amount > 0"),
        CheckConstraint("status IN ('LINKED', 'PAID', 'REFUNDED')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    commerce_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commerce_orders.id"), nullable=False
    )
    relaypay_payment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
