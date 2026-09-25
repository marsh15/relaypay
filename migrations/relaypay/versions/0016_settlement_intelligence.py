"""settlement intelligence policies forecasts and questions

Revision ID: 0016_settlement_intelligence
Revises: 0015_subscription_recovery
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_settlement_intelligence"
down_revision: str | None = "0015_subscription_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def scope_foreign_key() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organisation_id", "environment_id"],
        ["environments.organisation_id", "environments.id"],
    )


def account_foreign_key() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organisation_id", "environment_id", "merchant_account_id"],
        [
            "merchant_accounts.organisation_id",
            "merchant_accounts.environment_id",
            "merchant_accounts.id",
        ],
    )


def timestamps(*, updated: bool = False) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [sa.Column("id", sa.Uuid(), nullable=False)]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            )
        )
    columns.append(
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
    )
    return columns


def upgrade() -> None:
    op.create_table(
        "settlement_policies",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("timezone_name", sa.String(64), nullable=False),
        sa.Column("cutoff_hour", sa.Integer(), nullable=False),
        sa.Column("cutoff_minute", sa.Integer(), nullable=False),
        sa.Column("settlement_delay_days", sa.Integer(), nullable=False),
        sa.Column("weekend_handling", sa.String(8), nullable=False),
        sa.Column("policy_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *timestamps(),
        sa.CheckConstraint("cutoff_hour BETWEEN 0 AND 23"),
        sa.CheckConstraint("cutoff_minute BETWEEN 0 AND 59"),
        sa.CheckConstraint("settlement_delay_days BETWEEN 0 AND 2"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RETIRED')"),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("weekend_handling IN ('INCLUDE', 'SKIP')"),
        scope_foreign_key(),
        account_foreign_key(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "merchant_account_id", "version"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "uq_settlement_policies_active",
        "settlement_policies",
        ["organisation_id", "environment_id", "merchant_account_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "settlement_forecasts",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_arrival_date", sa.Date(), nullable=False),
        sa.Column("capture_total", sa.BigInteger(), nullable=False),
        sa.Column("refund_total", sa.BigInteger(), nullable=False),
        sa.Column("receivable_offset_total", sa.BigInteger(), nullable=False),
        sa.Column("expected_settlement_amount", sa.BigInteger(), nullable=False),
        sa.Column("capture_count", sa.Integer(), nullable=False),
        sa.Column("refund_count", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_sha256", sa.LargeBinary(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("capture_count >= 0 AND refund_count >= 0"),
        sa.CheckConstraint("capture_total >= 0 AND refund_total >= 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint(
            "expected_settlement_amount = capture_total - refund_total - receivable_offset_total"
        ),
        sa.CheckConstraint("expected_settlement_amount >= 0"),
        sa.CheckConstraint("receivable_offset_total >= 0"),
        sa.CheckConstraint("sequence > 0"),
        scope_foreign_key(),
        account_foreign_key(),
        sa.ForeignKeyConstraint(["policy_id"], ["settlement_policies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint(
            "organisation_id", "environment_id", "merchant_account_id", "business_date", "sequence"
        ),
    )
    op.create_index(
        "ix_settlement_forecasts_scope_created",
        "settlement_forecasts",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.create_table(
        "settlement_forecast_items",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("forecast_id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.String(24), nullable=False),
        sa.Column("capture_id", sa.Uuid(), nullable=True),
        sa.Column("refund_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        *timestamps(),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("item_type IN ('CAPTURE', 'REFUND', 'RECEIVABLE_OFFSET')"),
        sa.CheckConstraint(
            "(item_type = 'CAPTURE' AND amount > 0 AND capture_id IS NOT NULL "
            "AND refund_id IS NULL) OR "
            "(item_type = 'REFUND' AND amount < 0 AND refund_id IS NOT NULL) OR "
            "(item_type = 'RECEIVABLE_OFFSET' AND amount < 0 AND capture_id IS NULL "
            "AND refund_id IS NULL)"
        ),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "capture_id"],
            ["captures.organisation_id", "captures.environment_id", "captures.id"],
        ),
        sa.ForeignKeyConstraint(["forecast_id"], ["settlement_forecasts.id"]),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "refund_id"],
            ["refunds.organisation_id", "refunds.environment_id", "refunds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "uq_settlement_forecast_items_capture",
        "settlement_forecast_items",
        ["forecast_id", "capture_id"],
        unique=True,
        postgresql_where=sa.text("capture_id IS NOT NULL"),
    )
    op.create_index(
        "uq_settlement_forecast_items_refund",
        "settlement_forecast_items",
        ["forecast_id", "refund_id"],
        unique=True,
        postgresql_where=sa.text("refund_id IS NOT NULL"),
    )
    op.create_index(
        "uq_settlement_forecast_items_offset",
        "settlement_forecast_items",
        ["forecast_id"],
        unique=True,
        postgresql_where=sa.text("item_type = 'RECEIVABLE_OFFSET'"),
    )
    op.create_table(
        "settlement_questions",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("question_digest", sa.LargeBinary(), nullable=False),
        sa.Column("idempotency_key_digest", sa.LargeBinary(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("classification", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("classification_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("answer_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(updated=True),
        sa.CheckConstraint(
            "intent IN ('SETTLEMENT_LOWER', 'UNSETTLED_PAYMENTS', 'REFUND_IMPACT', "
            "'ARRIVAL_TOMORROW', 'CLARIFICATION')"
        ),
        sa.CheckConstraint("octet_length(idempotency_key_digest) = 32"),
        sa.CheckConstraint("octet_length(question_digest) = 32"),
        sa.CheckConstraint("status IN ('PROCESSING', 'ANSWERED', 'CLARIFICATION', 'FAILED')"),
        scope_foreign_key(),
        account_foreign_key(),
        sa.ForeignKeyConstraint(["policy_id"], ["settlement_policies.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
        sa.UniqueConstraint("organisation_id", "environment_id", "question_digest"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_settlement_questions_scope_created",
        "settlement_questions",
        ["organisation_id", "environment_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_settlement_questions_scope_created", table_name="settlement_questions")
    op.drop_table("settlement_questions")
    op.drop_index("uq_settlement_forecast_items_offset", table_name="settlement_forecast_items")
    op.drop_index("uq_settlement_forecast_items_refund", table_name="settlement_forecast_items")
    op.drop_index("uq_settlement_forecast_items_capture", table_name="settlement_forecast_items")
    op.drop_table("settlement_forecast_items")
    op.drop_index("ix_settlement_forecasts_scope_created", table_name="settlement_forecasts")
    op.drop_table("settlement_forecasts")
    op.drop_index("uq_settlement_policies_active", table_name="settlement_policies")
    op.drop_table("settlement_policies")
