"""UPI collect in the provider simulator: a pending authorization with a next action (M17).

Revision ID: 0006_upi_collect
Revises: 0005_chaos_webhooks
Create Date: 2026-11-19

A UPI collect authorization is PENDING until the customer approves the request on their phone. The
simulator models that with a ``UPI_COLLECT`` one-shot fault directive: the effect is created
PENDING with a ``next_action`` (what the customer is being asked to do) and a ``resolve_at`` time,
and the first lookup or replay after that time settles it to SUCCEEDED. The next action is part of
the signed response, so RelayPay sees it as provider evidence.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_upi_collect"
down_revision: str | None = "0005_chaos_webhooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_FAULTS = (
    "LOSE_RESPONSE",
    "DECLINE",
    "MALFORMED",
    "UNSIGNED",
    "MISMATCHED",
    "PENDING",
    "SLOW_RESPONSE",
)
NEW_FAULTS = (*OLD_FAULTS, "UPI_COLLECT")


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column("provider_effects", sa.Column("next_action", postgresql.JSONB(), nullable=True))
    op.add_column(
        "provider_effects", sa.Column("resolve_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "provider_effects_next_action_check",
        "provider_effects",
        "next_action IS NULL OR (operation_kind = 'AUTHORIZE' AND outcome = 'PENDING')",
    )
    op.drop_constraint(
        "provider_fault_directives_fault_type_check", "provider_fault_directives", type_="check"
    )
    op.create_check_constraint(
        "provider_fault_directives_fault_type_check",
        "provider_fault_directives",
        f"fault_type IN ({_in_list(NEW_FAULTS)})",
    )


def downgrade() -> None:
    op.execute("DELETE FROM provider_fault_directives WHERE fault_type = 'UPI_COLLECT'")
    op.drop_constraint(
        "provider_fault_directives_fault_type_check", "provider_fault_directives", type_="check"
    )
    op.create_check_constraint(
        "provider_fault_directives_fault_type_check",
        "provider_fault_directives",
        f"fault_type IN ({_in_list(OLD_FAULTS)})",
    )
    op.drop_constraint("provider_effects_next_action_check", "provider_effects", type_="check")
    op.drop_column("provider_effects", "resolve_at")
    op.drop_column("provider_effects", "next_action")
