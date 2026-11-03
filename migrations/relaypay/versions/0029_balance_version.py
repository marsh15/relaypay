"""Add the optimistic-locking version to merchant accounts (M19).

Revision ID: 0029_balance_version
Revises: 0028_forecast_capture_index
Create Date: 2026-11-04

``merchant_accounts.balance_version`` is the compare-and-swap token for the optimistic payout
reservation strategy: a reserver reads it with the balances and bumps it in the same transaction
as its reservation, so a concurrent reservation that read the same version loses and retries.
Existing rows start at 0. Nothing else reads or writes the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_balance_version"
down_revision: str | None = "0028_forecast_capture_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "merchant_accounts",
        sa.Column("balance_version", sa.BigInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("merchant_accounts", "balance_version")
