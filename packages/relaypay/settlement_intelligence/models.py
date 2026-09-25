import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


def scope_constraint() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organisation_id", "environment_id"],
        ["environments.organisation_id", "environments.id"],
    )


def account_constraint() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organisation_id", "environment_id", "merchant_account_id"],
        [
            "merchant_accounts.organisation_id",
            "merchant_accounts.environment_id",
            "merchant_accounts.id",
        ],
    )


class SettlementPolicy(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "settlement_policies"
    __table_args__ = (
        scope_constraint(),
        account_constraint(),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "merchant_account_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("cutoff_hour BETWEEN 0 AND 23"),
        CheckConstraint("cutoff_minute BETWEEN 0 AND 59"),
        CheckConstraint("settlement_delay_days BETWEEN 0 AND 2"),
        CheckConstraint("weekend_handling IN ('INCLUDE', 'SKIP')"),
        CheckConstraint("status IN ('ACTIVE', 'RETIRED')"),
        Index(
            "uq_settlement_policies_active",
            "organisation_id",
            "environment_id",
            "merchant_account_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    settlement_delay_days: Mapped[int] = mapped_column(Integer, nullable=False)
    weekend_handling: Mapped[str] = mapped_column(String(8), nullable=False)
    policy_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class SettlementForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "settlement_forecasts"
    __table_args__ = (
        scope_constraint(),
        account_constraint(),
        ForeignKeyConstraint(["policy_id"], ["settlement_policies.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint(
            "organisation_id", "environment_id", "merchant_account_id", "business_date", "sequence"
        ),
        CheckConstraint("sequence > 0"),
        CheckConstraint("capture_total >= 0 AND refund_total >= 0"),
        CheckConstraint("receivable_offset_total >= 0"),
        CheckConstraint("expected_settlement_amount >= 0"),
        CheckConstraint(
            "expected_settlement_amount = capture_total - refund_total - receivable_offset_total"
        ),
        CheckConstraint("capture_count >= 0 AND refund_count >= 0"),
        CheckConstraint("currency = 'INR'"),
        Index(
            "ix_settlement_forecasts_scope_created",
            "organisation_id",
            "environment_id",
            "created_at",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_arrival_date: Mapped[date] = mapped_column(Date, nullable=False)
    capture_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refund_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    receivable_offset_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_settlement_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capture_count: Mapped[int] = mapped_column(Integer, nullable=False)
    refund_count: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class SettlementForecastItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "settlement_forecast_items"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["forecast_id"], ["settlement_forecasts.id"]),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "capture_id"],
            ["captures.organisation_id", "captures.environment_id", "captures.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "refund_id"],
            ["refunds.organisation_id", "refunds.environment_id", "refunds.id"],
        ),
        UniqueConstraint("public_id"),
        Index(
            "uq_settlement_forecast_items_capture",
            "forecast_id",
            "capture_id",
            unique=True,
            postgresql_where=text("capture_id IS NOT NULL"),
        ),
        Index(
            "uq_settlement_forecast_items_refund",
            "forecast_id",
            "refund_id",
            unique=True,
            postgresql_where=text("refund_id IS NOT NULL"),
        ),
        Index(
            "uq_settlement_forecast_items_offset",
            "forecast_id",
            unique=True,
            postgresql_where=text("item_type = 'RECEIVABLE_OFFSET'"),
        ),
        CheckConstraint("item_type IN ('CAPTURE', 'REFUND', 'RECEIVABLE_OFFSET')"),
        CheckConstraint(
            "(item_type = 'CAPTURE' AND amount > 0 AND capture_id IS NOT NULL "
            "AND refund_id IS NULL) OR "
            "(item_type = 'REFUND' AND amount < 0 AND refund_id IS NOT NULL) OR "
            "(item_type = 'RECEIVABLE_OFFSET' AND amount < 0 AND capture_id IS NULL "
            "AND refund_id IS NULL)"
        ),
        CheckConstraint("currency = 'INR'"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    forecast_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    item_type: Mapped[str] = mapped_column(String(24), nullable=False)
    capture_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class SettlementQuestion(UUIDPrimaryKeyMixin, UpdatedAtMixin, CreatedAtMixin, Base):
    __tablename__ = "settlement_questions"
    __table_args__ = (
        scope_constraint(),
        account_constraint(),
        ForeignKeyConstraint(["policy_id"], ["settlement_policies.id"]),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "question_digest"),
        UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
        CheckConstraint(
            "intent IN ('SETTLEMENT_LOWER', 'UNSETTLED_PAYMENTS', 'REFUND_IMPACT', "
            "'ARRIVAL_TOMORROW', 'CLARIFICATION')"
        ),
        CheckConstraint("status IN ('PROCESSING', 'ANSWERED', 'CLARIFICATION', 'FAILED')"),
        CheckConstraint("octet_length(question_digest) = 32"),
        CheckConstraint("octet_length(idempotency_key_digest) = 32"),
        Index(
            "ix_settlement_questions_scope_created",
            "organisation_id",
            "environment_id",
            "created_at",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    question_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    idempotency_key_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    classification: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    classification_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    answer: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    answer_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
