"""Raw inbox for inbound PSP webhooks (M17).

Revision ID: 0026_psp_inbox
Revises: 0025_fees_gst
Create Date: 2026-10-21

``psp_events`` stores each signature-verified PSP webhook exactly once: unique on
``(provider, provider_event_id)``. The raw bytes and their digest are kept, together with the
operation the event names (matched by stable key at ingest). An event naming no known operation
is kept as UNMATCHED, never dropped, so reconciliation can see it. The bytes and identity are
frozen by the column-scoped trigger from 0018; only the processing lifecycle may change, and rows
are never deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_psp_inbox"
down_revision: str | None = "0025_fees_gst"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MUTABLE_COLUMNS = (
    "status",
    "outcome_code",
    "attempt_count",
    "next_attempt_at",
    "lease_token",
    "lease_expires_at",
    "processed_at",
    "updated_at",
)


def upgrade() -> None:
    op.create_table(
        "psp_events",
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("stable_key", sa.String(length=128), nullable=True),
        sa.Column("signature_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("payload_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("provider_operation_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outcome_code", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "provider IN ('SIMULATOR', 'RAZORPAY', 'CASHFREE')", name="psp_events_provider_check"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'APPLIED', 'IGNORED', 'DEFERRED', 'REVIEW', "
            "'UNMATCHED', 'DEAD_LETTER')",
            name="psp_events_status_check",
        ),
        sa.CheckConstraint("octet_length(payload_sha256) = 32", name="psp_events_digest_check"),
        sa.CheckConstraint(
            "(provider_operation_id IS NULL) = (status = 'UNMATCHED')",
            name="psp_events_match_check",
        ),
        sa.CheckConstraint(
            "(organisation_id IS NULL) = (provider_operation_id IS NULL) "
            "AND (environment_id IS NULL) = (provider_operation_id IS NULL)",
            name="psp_events_tenant_check",
        ),
        sa.ForeignKeyConstraint(["provider_operation_id"], ["provider_operations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index("ix_psp_events_claim", "psp_events", ["status", "next_attempt_at"])
    op.create_index(
        "ix_psp_events_operation", "psp_events", ["provider_operation_id", "created_at"]
    )
    arguments = ", ".join(f"'{column}'" for column in MUTABLE_COLUMNS)
    op.execute(
        "CREATE TRIGGER psp_events_frozen_columns BEFORE UPDATE OR DELETE ON psp_events "
        f"FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_frozen_column_mutation({arguments})"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER psp_events_frozen_columns ON psp_events")
    op.drop_index("ix_psp_events_operation", table_name="psp_events")
    op.drop_index("ix_psp_events_claim", table_name="psp_events")
    op.drop_table("psp_events")
