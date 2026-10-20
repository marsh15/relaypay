"""Provider effects and statements may record VOID (release of an authorization hold).

Revision ID: 0004_void_kind
Revises: 0003_slow_fault
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_void_kind"
down_revision: str | None = "0003_slow_fault"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_V2 = "operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND', 'VOID')"
KINDS_V1 = "operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')"
TABLES = ("provider_effects", "provider_statement_items")


def _replace(check: str) -> None:
    for table in TABLES:
        op.drop_constraint(f"{table}_operation_kind_check", table, type_="check")
        op.create_check_constraint(f"{table}_operation_kind_check", table, check)


def upgrade() -> None:
    _replace(KINDS_V2)


def downgrade() -> None:
    _replace(KINDS_V1)
