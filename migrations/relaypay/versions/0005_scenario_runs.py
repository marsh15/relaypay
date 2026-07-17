"""Durable synthetic scenario run evidence.

Revision ID: 0005_scenarios
Revises: 0004_delivery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_scenarios"
down_revision: str | None = "0004_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario_runs",
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=True),
        sa.Column("scenario_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assertions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("scenario_type = 'LOST_CAPTURE_RESPONSE'"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)"
        ),
        sa.CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'NEEDS_INSPECTION')"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_id"),
        sa.UniqueConstraint("organisation_id", "id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_scenario_runs_tenant_created",
        "scenario_runs",
        ["organisation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scenario_runs_tenant_created", table_name="scenario_runs")
    op.drop_table("scenario_runs")
