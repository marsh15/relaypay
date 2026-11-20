"""Requires-action authorizations and a pending deadline for provider operations (M17).

Revision ID: 0034_pending_action
Revises: 0033_recon_scale
Create Date: 2026-11-19

A UPI collect leaves an authorization PENDING until the customer approves it on their phone. The
provider's signed response says so with a ``nextAction``: ``authorizations.next_action`` records it
(and the payment moves to ``requires_action``) until the authorization reaches a terminal state.

Waiting for a person is not the same as an ambiguous provider. The five-lookup review budget
exists to stop RelayPay polling forever when the provider will not answer; an operation that the
provider has said is waiting for the customer gets ``pending_deadline_at`` instead. Until the
deadline its pending lookups cost nothing, and after it the operation goes to review
(``PENDING_DEADLINE_EXCEEDED``) so a human decides.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_pending_action"
down_revision: str | None = "0033_recon_scale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provider_operations",
        sa.Column("pending_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("authorizations", sa.Column("next_action", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "authorizations_next_action_check",
        "authorizations",
        "next_action IS NULL OR status IN ('PROCESSING', 'REQUIRES_REVIEW')",
    )


def downgrade() -> None:
    op.drop_constraint("authorizations_next_action_check", "authorizations", type_="check")
    op.drop_column("authorizations", "next_action")
    op.drop_column("provider_operations", "pending_deadline_at")
