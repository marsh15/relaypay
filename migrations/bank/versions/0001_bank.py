"""Synthetic bank accounts, effects, and fault directives.

Revision ID: 0001_bank
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_bank"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("signing_secret_digest", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_table(
        "bank_effects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_account_id", sa.Uuid(), nullable=False),
        sa.Column("stable_key", sa.String(128), nullable=False),
        sa.Column("beneficiary_reference", sa.String(128), nullable=False),
        sa.Column("payout_reference", sa.String(128), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("request_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("decline_code", sa.String(64), nullable=True),
        sa.Column("response_bytes", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount > 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("outcome IN ('PENDING', 'SUCCEEDED', 'DECLINED')"),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bank_account_id", "stable_key"),
    )
    op.create_table(
        "bank_fault_directives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_account_id", sa.Uuid(), nullable=False),
        sa.Column("stable_key", sa.String(128), nullable=False),
        sa.Column("fault_type", sa.String(24), nullable=False),
        sa.Column("remaining_uses", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("fault_type IN ('LOSE_RESPONSE', 'DECLINE', 'PENDING')"),
        sa.CheckConstraint("remaining_uses >= 0"),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bank_account_id", "stable_key", "fault_type"),
    )


def downgrade() -> None:
    op.drop_table("bank_fault_directives")
    op.drop_table("bank_effects")
    op.drop_table("bank_accounts")
