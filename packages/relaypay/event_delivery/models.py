import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class MerchantEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "merchant_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("provider_operation_id", "event_type"),
        CheckConstraint(
            "event_type IN ('payment.authorized.v1', 'payment.captured.v1', 'refund.succeeded.v1')"
        ),
        CheckConstraint("schema_version = 1"),
        Index(
            "ix_merchant_events_payment_occurred",
            "organisation_id",
            "payment_intent_id",
            "occurred_at",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1)
    event_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
