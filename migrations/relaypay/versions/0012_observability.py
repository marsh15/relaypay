"""bounded request logs, usage rollups, and payment-list query index

Revision ID: 0012_observability
Revises: 0011_connectors
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_observability"
down_revision: str | None = "0011_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_logs",
        sa.Column("request_id", sa.String(length=96), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("route", sa.String(length=256), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("duration_ms >= 0"),
        sa.CheckConstraint("status_code BETWEEN 100 AND 599"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_request_logs_created", "request_logs", ["created_at", "id"])
    op.create_index(
        "ix_request_logs_scope_created",
        "request_logs",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.create_table(
        "usage_rollups",
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("route", sa.String(length=256), nullable=False),
        sa.Column("status_class", sa.String(length=3), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms_total", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms_max", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("duration_ms_max >= 0"),
        sa.CheckConstraint("duration_ms_total >= 0"),
        sa.CheckConstraint("request_count > 0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bucket_start",
            "scope_key",
            "method",
            "route",
            "status_class",
        ),
    )
    op.create_index("ix_usage_rollups_bucket", "usage_rollups", ["bucket_start"])
    op.create_index(
        "ix_payment_intents_scope_created_id",
        "payment_intents",
        ["organisation_id", "environment_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_intents_scope_created_id", table_name="payment_intents")
    op.drop_index("ix_usage_rollups_bucket", table_name="usage_rollups")
    op.drop_table("usage_rollups")
    op.drop_index("ix_request_logs_scope_created", table_name="request_logs")
    op.drop_index("ix_request_logs_created", table_name="request_logs")
    op.drop_table("request_logs")
