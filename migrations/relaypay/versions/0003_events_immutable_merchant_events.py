"""Immutable merchant event evidence required by the shared finalizer.

Revision ID: 0003_events
Revises: 0002_payments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_events"
down_revision: str | None = "0002_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchant_events",
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=False),
        sa.Column("provider_operation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("event_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('payment.authorized.v1', 'payment.captured.v1', 'refund.succeeded.v1')"
        ),
        sa.CheckConstraint("schema_version = 1"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "id"),
        sa.UniqueConstraint("provider_operation_id", "event_type"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_merchant_events_payment_occurred",
        "merchant_events",
        ["organisation_id", "payment_intent_id", "occurred_at"],
    )
    op.execute(
        """
        CREATE TRIGGER merchant_events_immutable
          BEFORE UPDATE OR DELETE ON merchant_events
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation();
        """
    )


def downgrade() -> None:
    op.drop_index("ix_merchant_events_payment_occurred", table_name="merchant_events")
    op.drop_table("merchant_events")
