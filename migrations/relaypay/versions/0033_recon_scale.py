"""Reconciliation at scale: streamed imports, fee and tax columns, resumable chunked runs (M18).

Revision ID: 0033_recon_scale
Revises: 0032_rate_limit_gcra
Create Date: 2026-11-19

A streamed statement import is loaded with COPY and never held in memory, so it stores no raw
bytes (``raw_bytes`` becomes nullable; its digest is still recorded). ``statement_items`` gains the
optional fee and tax the provider charged. ``reconciliation_runs`` gains the progress cursor and
counters that let an algorithm-2 run resume after a crash, and the mismatch type list learns the
three new types (FEE_MISMATCH, TAX_MISMATCH, TIMING). Version-1 runs and their rows are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_recon_scale"
down_revision: str | None = "0032_rate_limit_gcra"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "MISSING_INTERNAL_TRANSACTION",
    "MISSING_PROVIDER_TRANSACTION",
    "AMOUNT_MISMATCH",
    "CURRENCY_MISMATCH",
    "STATUS_MISMATCH",
    "DUPLICATE_PROVIDER_EFFECT",
    "MISSING_INTERNAL_JOURNAL",
)
NEW_TYPES = (*OLD_TYPES, "FEE_MISMATCH", "TAX_MISMATCH", "TIMING")


def _in_list(types: Sequence[str]) -> str:
    return ", ".join(f"'{t}'" for t in types)


def upgrade() -> None:
    op.alter_column("statement_imports", "raw_bytes", nullable=True)
    op.add_column(
        "statement_imports",
        sa.Column("streamed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        "statement_imports_raw_or_streamed_check",
        "statement_imports",
        "streamed OR raw_bytes IS NOT NULL",
    )

    op.add_column("statement_items", sa.Column("fee_amount", sa.BigInteger(), nullable=True))
    op.add_column("statement_items", sa.Column("tax_amount", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "statement_items_fee_amount_check",
        "statement_items",
        "fee_amount IS NULL OR fee_amount >= 0",
    )
    op.create_check_constraint(
        "statement_items_tax_amount_check",
        "statement_items",
        "tax_amount IS NULL OR tax_amount >= 0",
    )

    op.add_column(
        "reconciliation_runs",
        sa.Column("progress_ordinal", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "reconciliation_runs",
        sa.Column("matched_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "reconciliation_runs",
        sa.Column("mismatch_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        "reconciliation_runs_progress_ordinal_check",
        "reconciliation_runs",
        "progress_ordinal >= 0",
    )

    op.drop_constraint(
        "reconciliation_mismatches_mismatch_type_check", "reconciliation_mismatches", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_mismatches_mismatch_type_check",
        "reconciliation_mismatches",
        f"mismatch_type IN ({_in_list(NEW_TYPES)})",
    )

    # created_at leads on purpose. An index starting (organisation_id, environment_id) shares its
    # prefix with every foreign key into provider_operations, and the planner then picks it for
    # the (organisation_id, environment_id, id) existence check and scans a whole tenant per row.
    op.create_index(
        "ix_provider_operations_created_scope",
        "provider_operations",
        ["created_at", "organisation_id", "environment_id"],
    )


def downgrade() -> None:
    new_only = ", ".join(f"'{t}'" for t in ("FEE_MISMATCH", "TAX_MISMATCH", "TIMING"))
    bind = op.get_bind()
    blocking = bind.scalar(
        sa.text(
            f"SELECT (SELECT count(*) FROM reconciliation_mismatches WHERE mismatch_type IN "  # noqa: S608
            f"({new_only})) + (SELECT count(*) FROM statement_imports WHERE streamed)"
        )
    )
    if blocking:
        raise RuntimeError(
            f"{blocking} streamed imports or v2 mismatches exist; they are immutable evidence "
            "and the 0033 downgrade will not delete them"
        )
    op.drop_index("ix_provider_operations_created_scope", table_name="provider_operations")
    op.drop_constraint(
        "reconciliation_mismatches_mismatch_type_check", "reconciliation_mismatches", type_="check"
    )
    op.create_check_constraint(
        "reconciliation_mismatches_mismatch_type_check",
        "reconciliation_mismatches",
        f"mismatch_type IN ({_in_list(OLD_TYPES)})",
    )
    op.drop_constraint(
        "reconciliation_runs_progress_ordinal_check", "reconciliation_runs", type_="check"
    )
    op.drop_column("reconciliation_runs", "mismatch_count")
    op.drop_column("reconciliation_runs", "matched_count")
    op.drop_column("reconciliation_runs", "progress_ordinal")
    op.drop_constraint("statement_items_tax_amount_check", "statement_items", type_="check")
    op.drop_constraint("statement_items_fee_amount_check", "statement_items", type_="check")
    op.drop_column("statement_items", "tax_amount")
    op.drop_column("statement_items", "fee_amount")
    op.drop_constraint(
        "statement_imports_raw_or_streamed_check", "statement_imports", type_="check"
    )
    op.drop_column("statement_imports", "streamed")
    op.alter_column("statement_imports", "raw_bytes", nullable=False)
