"""Immutable payment-provider statement exports.

Revision ID: 0002_provider_statements
Revises: 0001_provider
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_provider_statements"
down_revision: str | None = "0001_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_statement_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("provider_account_id", sa.Uuid(), nullable=False),
        sa.Column("source_reference", sa.String(128), nullable=False),
        sa.Column("source_format", sa.String(8), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("raw_sha256", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source_format IN ('CSV', 'JSON')"),
        sa.CheckConstraint("period_end > period_start"),
        sa.CheckConstraint("octet_length(raw_bytes) BETWEEN 1 AND 1048576"),
        sa.CheckConstraint("octet_length(raw_sha256) = 32"),
        sa.ForeignKeyConstraint(["provider_account_id"], ["provider_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("provider_account_id", "source_reference"),
    )
    op.create_table(
        "provider_statement_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("statement_export_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("provider_item_id", sa.String(128), nullable=False),
        sa.Column("stable_key", sa.String(128), nullable=False),
        sa.Column("operation_kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider_status", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal > 0"),
        sa.CheckConstraint("operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')"),
        sa.CheckConstraint("amount > 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("provider_status IN ('PENDING', 'SUCCEEDED', 'DECLINED')"),
        sa.ForeignKeyConstraint(["statement_export_id"], ["provider_statement_exports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("statement_export_id", "ordinal"),
        sa.UniqueConstraint("statement_export_id", "provider_item_id"),
    )
    op.execute(
        """
        CREATE FUNCTION provider_prevent_statement_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$;
        CREATE TRIGGER provider_statement_exports_immutable
          BEFORE UPDATE OR DELETE ON provider_statement_exports
          FOR EACH ROW EXECUTE FUNCTION provider_prevent_statement_mutation();
        CREATE TRIGGER provider_statement_items_immutable
          BEFORE UPDATE OR DELETE ON provider_statement_items
          FOR EACH ROW EXECUTE FUNCTION provider_prevent_statement_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS provider_prevent_statement_mutation() CASCADE")
    op.drop_table("provider_statement_items")
    op.drop_table("provider_statement_exports")
