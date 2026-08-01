"""add bounded slow-response provider fault

Revision ID: 0003_slow_fault
Revises: 0002_provider_statements
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_slow_fault"
down_revision: str | None = "0002_provider_statements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "provider_fault_directives_fault_type_check",
        "provider_fault_directives",
        type_="check",
    )
    op.create_check_constraint(
        "provider_fault_directives_fault_type_check",
        "provider_fault_directives",
        "fault_type IN ('LOSE_RESPONSE', 'DECLINE', 'MALFORMED', 'UNSIGNED', "
        "'MISMATCHED', 'PENDING', 'SLOW_RESPONSE')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM provider_fault_directives WHERE fault_type = 'SLOW_RESPONSE'")
    op.drop_constraint(
        "provider_fault_directives_fault_type_check",
        "provider_fault_directives",
        type_="check",
    )
    op.create_check_constraint(
        "provider_fault_directives_fault_type_check",
        "provider_fault_directives",
        "fault_type IN ('LOSE_RESPONSE', 'DECLINE', 'MALFORMED', 'UNSIGNED', "
        "'MISMATCHED', 'PENDING')",
    )
