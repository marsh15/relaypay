"""Permit statement currency evidence that can disagree with INR-only internal payments.

Revision ID: 0008_statement_currency
Revises: 0007_reconciliation
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_statement_currency"
down_revision: str | None = "0007_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("statement_items_currency_check", "statement_items", type_="check")
    op.create_check_constraint(
        "statement_items_currency_check",
        "statement_items",
        "currency ~ '^[A-Z]{3}$'",
    )


def downgrade() -> None:
    op.drop_constraint("statement_items_currency_check", "statement_items", type_="check")
    op.create_check_constraint(
        "statement_items_currency_check", "statement_items", "currency = 'INR'"
    )
