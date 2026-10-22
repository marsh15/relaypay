"""Seeded chaos profiles and a signed webhook outbox for the provider simulator (M17).

Revision ID: 0005_chaos_webhooks
Revises: 0004_void_kind
Create Date: 2026-10-21

* ``provider_chaos_profiles``: one per account. Fault probabilities are basis points; every
  decision is drawn from ``sha256(seed | stable key | aspect)``, so a run replays from its seed.
  One-shot fault directives still take precedence.
* ``provider_webhook_outbox``: one ``effect.updated`` event per effect, written in the effect's
  transaction and pushed to RelayPay's PSP inbox. The profile may drop, duplicate or delay it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_chaos_webhooks"
down_revision: str | None = "0004_void_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BPS_COLUMNS = (
    "error_before_bps",
    "error_after_bps",
    "lose_response_bps",
    "slow_bps",
    "pending_bps",
    "decline_bps",
    "webhook_drop_bps",
    "webhook_duplicate_bps",
)


def upgrade() -> None:
    op.create_table(
        "provider_chaos_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_account_id", sa.Uuid(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        *(sa.Column(column, sa.Integer(), nullable=False) for column in BPS_COLUMNS[:4]),
        sa.Column("slow_ms", sa.Integer(), nullable=False),
        *(sa.Column(column, sa.Integer(), nullable=False) for column in BPS_COLUMNS[4:]),
        sa.Column("webhook_max_delay_ms", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            " AND ".join(f"{column} BETWEEN 0 AND 10000" for column in BPS_COLUMNS),
            name="provider_chaos_profiles_bps_check",
        ),
        sa.CheckConstraint(
            "slow_ms BETWEEN 0 AND 10000 AND webhook_max_delay_ms BETWEEN 0 AND 600000",
            name="provider_chaos_profiles_timing_check",
        ),
        sa.ForeignKeyConstraint(["provider_account_id"], ["provider_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_account_id"),
    )
    op.create_table(
        "provider_webhook_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_account_id", sa.Uuid(), nullable=False),
        sa.Column("provider_effect_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("payload_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("copies_remaining", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'DELIVERED', 'DROPPED', 'DEAD')",
            name="provider_webhook_outbox_status_check",
        ),
        sa.CheckConstraint("copies_remaining >= 0", name="provider_webhook_outbox_copies_check"),
        sa.CheckConstraint("attempt_count >= 0", name="provider_webhook_outbox_attempts_check"),
        sa.ForeignKeyConstraint(["provider_account_id"], ["provider_accounts.id"]),
        sa.ForeignKeyConstraint(["provider_effect_id"], ["provider_effects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        "ix_provider_webhook_outbox_due",
        "provider_webhook_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_webhook_outbox_due", table_name="provider_webhook_outbox")
    op.drop_table("provider_webhook_outbox")
    op.drop_table("provider_chaos_profiles")
