import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class FeeScheduleVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One immutable version of a merchant account's fee terms; a change appends a version."""

    __tablename__ = "fee_schedule_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        UniqueConstraint("merchant_account_id", "version"),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        CheckConstraint("version > 0", name="fee_schedule_versions_version_check"),
        CheckConstraint(
            "mdr_bps BETWEEN 0 AND 10000 AND gst_bps BETWEEN 0 AND 10000 AND fixed_paise >= 0",
            name="fee_schedule_versions_rates_check",
        ),
        CheckConstraint(
            "(min_fee_paise IS NULL OR min_fee_paise >= 0) AND "
            "(max_fee_paise IS NULL OR max_fee_paise >= 0) AND "
            "(min_fee_paise IS NULL OR max_fee_paise IS NULL OR min_fee_paise <= max_fee_paise)",
            name="fee_schedule_versions_bounds_check",
        ),
        CheckConstraint(
            "refund_fee_policy IN ('RETAIN')",
            name="fee_schedule_versions_refund_policy_check",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mdr_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    fixed_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    gst_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    min_fee_paise: Mapped[int | None] = mapped_column(Integer)
    max_fee_paise: Mapped[int | None] = mapped_column(Integer)
    refund_fee_policy: Mapped[str] = mapped_column(String(24), nullable=False)
