"""subscription recovery cases policy actions and communication evidence

Revision ID: 0015_subscription_recovery
Revises: 0014_disputes
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_subscription_recovery"
down_revision: str | None = "0014_disputes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def scope_foreign_key() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organisation_id", "environment_id"],
        ["environments.organisation_id", "environments.id"],
    )


def timestamps(*, updated: bool = False) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [sa.Column("id", sa.Uuid(), nullable=False)]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            )
        )
    columns.append(
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
    )
    return columns


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("plan_reference", sa.String(128), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("consent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("consent_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *timestamps(updated=True),
        sa.CheckConstraint("amount > 0 AND currency = 'INR'"),
        sa.CheckConstraint("status IN ('ACTIVE', 'PAST_DUE', 'RECOVERED', 'CANCELLED', 'EXPIRED')"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "external_id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_subscriptions_scope_created",
        "subscriptions",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.create_table(
        "subscription_invoices",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(updated=True),
        sa.CheckConstraint("amount > 0 AND currency = 'INR'"),
        sa.CheckConstraint("status IN ('OPEN', 'FAILED', 'PAID', 'VOID')"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("subscription_id", "external_id"),
    )
    op.create_index(
        "ix_subscription_invoices_scope_created",
        "subscription_invoices",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.create_table(
        "recurring_payment_attempts",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_attempt_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("failure_classification", sa.String(40), nullable=True),
        sa.Column("provider_code", sa.String(64), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.CheckConstraint("attempt_number > 0"),
        sa.CheckConstraint(
            "failure_classification IS NULL OR failure_classification IN "
            "('RETRYABLE_SOFT_DECLINE', 'NON_RETRYABLE_HARD_DECLINE', "
            "'AUTHENTICATION_REQUIRED', 'EXPIRED_METHOD', 'TRANSPORT_UNKNOWN')"
        ),
        sa.CheckConstraint(
            "outcome IN ('VERIFIED_FAILED', 'VERIFIED_SUCCEEDED', 'TRANSPORT_UNKNOWN')"
        ),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["invoice_id"], ["subscription_invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "attempt_number"),
        sa.UniqueConstraint("provider_attempt_id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_table(
        "recovery_policy_versions",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("max_payment_retries", sa.Integer(), nullable=False),
        sa.Column("max_messages", sa.Integer(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("retry_windows_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("message_windows_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *timestamps(),
        sa.CheckConstraint("max_messages BETWEEN 0 AND 3"),
        sa.CheckConstraint("max_payment_retries BETWEEN 0 AND 3"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RETIRED')"),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("window_days BETWEEN 1 AND 14"),
        scope_foreign_key(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "version"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_table(
        "recovery_cases",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("payment_retry_count", sa.Integer(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("termination_reason", sa.String(64), nullable=True),
        *timestamps(updated=True),
        sa.CheckConstraint("message_count BETWEEN 0 AND 3"),
        sa.CheckConstraint("payment_retry_count BETWEEN 0 AND 3"),
        sa.CheckConstraint(
            "status IN ('OPEN', 'SCHEDULED', 'RECOVERED', 'TERMINATED', 'EXPIRED', "
            "'REQUIRES_REVIEW')"
        ),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["invoice_id"], ["subscription_invoices.id"]),
        sa.ForeignKeyConstraint(["policy_version_id"], ["recovery_policy_versions.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["trigger_attempt_id"], ["recurring_payment_attempts.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_recovery_cases_scope_created",
        "recovery_cases",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.create_table(
        "scheduled_recovery_actions",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("recovery_case_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(24), nullable=False),
        sa.Column("channel", sa.String(16), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stable_key", sa.String(192), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_code", sa.String(64), nullable=True),
        sa.Column("response_sha256", sa.LargeBinary(), nullable=True),
        *timestamps(updated=True),
        sa.CheckConstraint("action_type IN ('PAYMENT_RETRY', 'MESSAGE')"),
        sa.CheckConstraint("channel IS NULL OR channel IN ('EMAIL', 'WHATSAPP', 'IN_APP')"),
        sa.CheckConstraint("sequence > 0"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED', 'EXECUTING', 'EXECUTED', 'AMBIGUOUS', 'CANCELLED', "
            "'SUPPRESSED')"
        ),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["recovery_case_id"], ["recovery_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("recovery_case_id", "sequence"),
        sa.UniqueConstraint("stable_key"),
    )
    op.create_index(
        "ix_scheduled_recovery_actions_due",
        "scheduled_recovery_actions",
        ["status", "scheduled_for", "created_at"],
    )
    op.create_table(
        "communication_records",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("recovery_case_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_action_id", sa.Uuid(), nullable=False),
        sa.Column("stable_key", sa.String(192), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("tokenized_body", sa.Text(), nullable=False),
        sa.Column("rendered_body", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_code", sa.String(64), nullable=True),
        sa.Column("response_sha256", sa.LargeBinary(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("channel IN ('EMAIL', 'WHATSAPP', 'IN_APP')"),
        sa.CheckConstraint("status IN ('SENT', 'AMBIGUOUS', 'DELIVERED', 'FAILED')"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["recovery_case_id"], ["recovery_cases.id"]),
        sa.ForeignKeyConstraint(["scheduled_action_id"], ["scheduled_recovery_actions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("scheduled_action_id"),
        sa.UniqueConstraint("stable_key"),
    )
    op.create_table(
        "recovery_opt_outs",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("recovery_case_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.CheckConstraint("channel IN ('ALL', 'EMAIL', 'WHATSAPP', 'IN_APP')"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["recovery_case_id"], ["recovery_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("source_event_id"),
    )


def downgrade() -> None:
    op.drop_table("recovery_opt_outs")
    op.drop_table("communication_records")
    op.drop_index("ix_scheduled_recovery_actions_due", table_name="scheduled_recovery_actions")
    op.drop_table("scheduled_recovery_actions")
    op.drop_index("ix_recovery_cases_scope_created", table_name="recovery_cases")
    op.drop_table("recovery_cases")
    op.drop_table("recovery_policy_versions")
    op.drop_table("recurring_payment_attempts")
    op.drop_index("ix_subscription_invoices_scope_created", table_name="subscription_invoices")
    op.drop_table("subscription_invoices")
    op.drop_index("ix_subscriptions_scope_created", table_name="subscriptions")
    op.drop_table("subscriptions")
