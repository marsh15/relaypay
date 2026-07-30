"""Synthetic commerce orders and payment links.

Revision ID: 0001_commerce
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_commerce"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commerce_accounts",
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
        "commerce_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("commerce_account_id", sa.Uuid(), nullable=False),
        sa.Column("external_reference", sa.String(128), nullable=False),
        sa.Column("total_amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("total_amount > 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("status IN ('OPEN', 'PAID', 'REFUNDED')"),
        sa.ForeignKeyConstraint(["commerce_account_id"], ["commerce_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("commerce_account_id", "external_reference"),
    )
    op.create_table(
        "commerce_payment_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("commerce_order_id", sa.Uuid(), nullable=False),
        sa.Column("relaypay_payment_id", sa.String(64), nullable=False),
        sa.Column("linked_amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("linked_amount > 0"),
        sa.CheckConstraint("status IN ('LINKED', 'PAID', 'REFUNDED')"),
        sa.ForeignKeyConstraint(["commerce_order_id"], ["commerce_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commerce_order_id", "relaypay_payment_id"),
    )


def downgrade() -> None:
    op.drop_table("commerce_payment_links")
    op.drop_table("commerce_orders")
    op.drop_table("commerce_accounts")
