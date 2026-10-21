"""PSP webhooks become provider evidence (M17).

Revision ID: 0027_webhook_evidence
Revises: 0026_psp_inbox
Create Date: 2026-10-21

A verified PSP webhook is recorded as a ``provider_attempts`` row with ``attempt_kind =
'WEBHOOK'`` and finalized by the one finalizer, so webhooks never become a second money path.
``operation_history.actor_type`` gains ``WEBHOOK`` for the transitions they cause. A partial
unique index allows each webhook payload to be recorded once per operation: the applier relies
on it to resume after a crash between recording and finalizing. No existing row changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_webhook_evidence"
down_revision: str | None = "0026_psp_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)


def upgrade() -> None:
    _replace_check(
        "provider_attempts",
        "provider_attempts_attempt_kind_check",
        "attempt_kind IN ('MUTATION', 'LOOKUP', 'WEBHOOK')",
    )
    _replace_check(
        "operation_history",
        "operation_history_actor_type_check",
        "actor_type IN ('REQUEST', 'RECOVERY_WORKER', 'ADMIN_LOOKUP', 'FINALIZER', 'WEBHOOK')",
    )
    op.create_index(
        "uq_provider_attempts_one_webhook_payload",
        "provider_attempts",
        ["provider_operation_id", "response_sha256"],
        unique=True,
        postgresql_where="attempt_kind = 'WEBHOOK'",
    )


def downgrade() -> None:
    op.drop_index("uq_provider_attempts_one_webhook_payload", table_name="provider_attempts")
    _replace_check(
        "operation_history",
        "operation_history_actor_type_check",
        "actor_type IN ('REQUEST', 'RECOVERY_WORKER', 'ADMIN_LOOKUP', 'FINALIZER')",
    )
    _replace_check(
        "provider_attempts",
        "provider_attempts_attempt_kind_check",
        "attempt_kind IN ('MUTATION', 'LOOKUP')",
    )
