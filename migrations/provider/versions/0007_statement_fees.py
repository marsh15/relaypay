"""Provider fee terms and fee/tax columns on statement exports (M18).

Revision ID: 0007_statement_fees
Revises: 0006_upi_collect
Create Date: 2026-11-20

A real PSP's settlement statement carries the fee it charged and the tax on that fee. The simulator
can now do the same: an account may be given fee terms (``POST /control/fee-terms``), and its
exports then carry ``feeAmount`` and ``taxAmount`` on every successful capture, computed with the
same integer rule RelayPay uses (rate in basis points of the amount plus a fixed amount, tax in
basis points of the fee, half-up). With no terms the export is byte-identical to before. Setting
terms that differ from a merchant's RelayPay schedule produces ``FEE_MISMATCH`` and
``TAX_MISMATCH`` on reconciliation, which is the point.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_statement_fees"
down_revision: str | None = "0006_upi_collect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("provider_accounts", sa.Column("fee_mdr_bps", sa.Integer(), nullable=True))
    op.add_column("provider_accounts", sa.Column("fee_fixed_paise", sa.Integer(), nullable=True))
    op.add_column("provider_accounts", sa.Column("fee_gst_bps", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "provider_accounts_fee_terms_check",
        "provider_accounts",
        "(fee_mdr_bps IS NULL AND fee_fixed_paise IS NULL AND fee_gst_bps IS NULL) OR "
        "(fee_mdr_bps BETWEEN 0 AND 10000 AND fee_fixed_paise >= 0 "
        "AND fee_gst_bps BETWEEN 0 AND 10000)",
    )
    op.add_column("provider_statement_items", sa.Column("fee_amount", sa.BigInteger()))
    op.add_column("provider_statement_items", sa.Column("tax_amount", sa.BigInteger()))
    op.create_check_constraint(
        "provider_statement_items_fee_check",
        "provider_statement_items",
        "(fee_amount IS NULL OR fee_amount >= 0) AND (tax_amount IS NULL OR tax_amount >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("provider_statement_items_fee_check", "provider_statement_items", "check")
    op.drop_column("provider_statement_items", "tax_amount")
    op.drop_column("provider_statement_items", "fee_amount")
    op.drop_constraint("provider_accounts_fee_terms_check", "provider_accounts", "check")
    op.drop_column("provider_accounts", "fee_gst_bps")
    op.drop_column("provider_accounts", "fee_fixed_paise")
    op.drop_column("provider_accounts", "fee_mdr_bps")
