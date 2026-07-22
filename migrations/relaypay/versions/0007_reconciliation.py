"""Immutable statements and leased reconciliation evidence.

Revision ID: 0007_reconciliation
Revises: 0006_identity_environments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_reconciliation"
down_revision: str | None = "0006_identity_environments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_TABLES = (
    "statement_imports",
    "statement_items",
    "reconciliation_matches",
    "mismatch_evidence_versions",
    "mismatch_workflow_history",
)


def _identity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _scope_foreign_key() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organisation_id", "environment_id"],
        ["environments.organisation_id", "environments.id"],
    )


def upgrade() -> None:
    op.create_table(
        "statement_imports",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("source_reference", sa.String(128), nullable=False),
        sa.Column("source_format", sa.String(8), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("raw_sha256", sa.LargeBinary(), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("provider = 'PAYMENT_PROVIDER'"),
        sa.CheckConstraint("source_format IN ('CSV', 'JSON')"),
        sa.CheckConstraint("period_end > period_start"),
        sa.CheckConstraint("octet_length(raw_bytes) BETWEEN 1 AND 1048576"),
        sa.CheckConstraint("octet_length(raw_sha256) = 32"),
        _scope_foreign_key(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "provider", "source_reference"),
    )
    op.create_index(
        "ix_statement_imports_scope_period",
        "statement_imports",
        ["organisation_id", "environment_id", "period_start", "period_end"],
    )
    op.create_table(
        "statement_items",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("statement_import_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("provider_item_id", sa.String(128), nullable=False),
        sa.Column("stable_key", sa.String(128), nullable=False),
        sa.Column("operation_kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("provider_status", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("ordinal > 0"),
        sa.CheckConstraint("operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')"),
        sa.CheckConstraint("amount > 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("provider_status IN ('PENDING', 'SUCCEEDED', 'DECLINED')"),
        _scope_foreign_key(),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_import_id"],
            [
                "statement_imports.organisation_id",
                "statement_imports.environment_id",
                "statement_imports.id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("statement_import_id", "ordinal"),
        sa.UniqueConstraint("statement_import_id", "provider_item_id"),
    )
    op.create_index(
        "ix_statement_items_stable_key", "statement_items", ["statement_import_id", "stable_key"]
    )
    op.create_table(
        "reconciliation_runs",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("statement_import_id", sa.Uuid(), nullable=False),
        sa.Column("algorithm_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("algorithm_version > 0"),
        sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')"),
        sa.CheckConstraint("attempt_count >= 0"),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'RUNNING' AND lease_token IS NULL AND lease_expires_at IS NULL)"
        ),
        sa.CheckConstraint(
            "(status IN ('COMPLETED', 'FAILED') AND completed_at IS NOT NULL) OR "
            "(status IN ('PENDING', 'RUNNING') AND completed_at IS NULL)"
        ),
        _scope_foreign_key(),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_import_id"],
            [
                "statement_imports.organisation_id",
                "statement_imports.environment_id",
                "statement_imports.id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("statement_import_id", "algorithm_version"),
    )
    op.create_index(
        "ix_reconciliation_runs_claim",
        "reconciliation_runs",
        ["status", "lease_expires_at", "created_at"],
    )
    op.create_table(
        "reconciliation_matches",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_run_id", sa.Uuid(), nullable=False),
        sa.Column("statement_item_id", sa.Uuid(), nullable=False),
        sa.Column("provider_operation_id", sa.Uuid(), nullable=False),
        sa.Column("journal_id", sa.Uuid(), nullable=True),
        sa.Column("match_type", sa.String(32), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("match_type IN ('EXACT', 'DECLINED_WITHOUT_JOURNAL')"),
        _scope_foreign_key(),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_run_id"],
            [
                "reconciliation_runs.organisation_id",
                "reconciliation_runs.environment_id",
                "reconciliation_runs.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_item_id"],
            [
                "statement_items.organisation_id",
                "statement_items.environment_id",
                "statement_items.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("reconciliation_run_id", "statement_item_id"),
    )
    op.create_table(
        "reconciliation_mismatches",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_run_id", sa.Uuid(), nullable=False),
        sa.Column("subject_key", sa.String(256), nullable=False),
        sa.Column("mismatch_type", sa.String(40), nullable=False),
        sa.Column("statement_item_id", sa.Uuid(), nullable=True),
        sa.Column("provider_operation_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_status", sa.String(16), nullable=False),
        sa.Column("acknowledgement_note", sa.String(1000), nullable=True),
        sa.Column("resolution_note", sa.String(1000), nullable=True),
        sa.Column("compensating_journal_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mismatch_type IN ('MISSING_INTERNAL_TRANSACTION', 'MISSING_PROVIDER_TRANSACTION', "
            "'AMOUNT_MISMATCH', 'CURRENCY_MISMATCH', 'STATUS_MISMATCH', "
            "'DUPLICATE_PROVIDER_EFFECT', 'MISSING_INTERNAL_JOURNAL')"
        ),
        sa.CheckConstraint("workflow_status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')"),
        sa.CheckConstraint(
            "(workflow_status = 'RESOLVED' AND resolved_at IS NOT NULL) OR "
            "(workflow_status <> 'RESOLVED' AND resolved_at IS NULL)"
        ),
        _scope_foreign_key(),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_run_id"],
            [
                "reconciliation_runs.organisation_id",
                "reconciliation_runs.environment_id",
                "reconciliation_runs.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_item_id"],
            [
                "statement_items.organisation_id",
                "statement_items.environment_id",
                "statement_items.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "compensating_journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("reconciliation_run_id", "subject_key"),
    )
    op.create_index(
        "ix_reconciliation_mismatches_scope_status",
        "reconciliation_mismatches",
        ["organisation_id", "environment_id", "workflow_status", "created_at"],
    )
    op.create_table(
        "mismatch_evidence_versions",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_mismatch_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_sha256", sa.LargeBinary(), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("octet_length(evidence_sha256) = 32"),
        _scope_foreign_key(),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_mismatch_id"],
            [
                "reconciliation_mismatches.organisation_id",
                "reconciliation_mismatches.environment_id",
                "reconciliation_mismatches.id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("reconciliation_mismatch_id", "version"),
    )
    op.create_table(
        "mismatch_workflow_history",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_mismatch_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("to_status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')"),
        sa.CheckConstraint("from_status IS NULL OR from_status IN ('OPEN', 'ACKNOWLEDGED')"),
        _scope_foreign_key(),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_mismatch_id"],
            [
                "reconciliation_mismatches.organisation_id",
                "reconciliation_mismatches.environment_id",
                "reconciliation_mismatches.id",
            ],
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
    )
    op.create_index(
        "ix_mismatch_workflow_history_order",
        "mismatch_workflow_history",
        ["reconciliation_mismatch_id", "created_at", "id"],
    )
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable
              BEFORE UPDATE OR DELETE ON {table}
              FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation()
            """
        )


def downgrade() -> None:
    for table in reversed(IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.drop_index("ix_mismatch_workflow_history_order", table_name="mismatch_workflow_history")
    op.drop_table("mismatch_workflow_history")
    op.drop_table("mismatch_evidence_versions")
    op.drop_index(
        "ix_reconciliation_mismatches_scope_status", table_name="reconciliation_mismatches"
    )
    op.drop_table("reconciliation_mismatches")
    op.drop_table("reconciliation_matches")
    op.drop_index("ix_reconciliation_runs_claim", table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
    op.drop_index("ix_statement_items_stable_key", table_name="statement_items")
    op.drop_table("statement_items")
    op.drop_index("ix_statement_imports_scope_period", table_name="statement_imports")
    op.drop_table("statement_imports")
