"""Receiver event deduplication store.

Revision ID: 0001_receiver
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_receiver"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "received_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_count", sa.Integer(), nullable=False),
        sa.Column("signature_timestamp", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("delivery_count > 0"),
        sa.PrimaryKeyConstraint("event_id"),
        schema="receiver",
    )


def downgrade() -> None:
    op.drop_table("received_events", schema="receiver")
