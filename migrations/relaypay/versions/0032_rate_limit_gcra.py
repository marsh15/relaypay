"""Per-API-key GCRA rate limiting (M19).

Revision ID: 0032_rate_limit_gcra
Revises: 0031_merchant_events_v2
Create Date: 2026-11-19

``api_rate_limit_state`` holds one theoretical arrival time per limiter key. It is UNLOGGED on
purpose: it is rewritten on every request, nothing depends on its durability, and losing it on a
crash only gives every key a fresh budget. ``api_keys`` gains optional per-key limits; NULL means
the deployment default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_rate_limit_gcra"
down_revision: str | None = "0031_merchant_events_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_rate_limit_state",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("tat", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        prefixes=["UNLOGGED"],
    )
    op.add_column("api_keys", sa.Column("rate_limit_rps", sa.Integer(), nullable=True))
    op.add_column("api_keys", sa.Column("rate_limit_burst", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "api_keys_rate_limit_rps_check",
        "api_keys",
        "rate_limit_rps IS NULL OR rate_limit_rps > 0",
    )
    op.create_check_constraint(
        "api_keys_rate_limit_burst_check",
        "api_keys",
        "rate_limit_burst IS NULL OR rate_limit_burst > 0",
    )


def downgrade() -> None:
    op.drop_constraint("api_keys_rate_limit_burst_check", "api_keys", type_="check")
    op.drop_constraint("api_keys_rate_limit_rps_check", "api_keys", type_="check")
    op.drop_column("api_keys", "rate_limit_burst")
    op.drop_column("api_keys", "rate_limit_rps")
    op.drop_table("api_rate_limit_state")
