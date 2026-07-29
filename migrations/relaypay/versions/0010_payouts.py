"""Reserved payouts, numbered bank attempts, and immutable recovery evidence.

Revision ID: 0010_payouts
Revises: 0009_merchant_balances
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_payouts"
down_revision: str | None = "0009_merchant_balances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps(*, updated: bool = False) -> list[sa.Column[object]]:
    columns: list[sa.Column[object]] = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
    ]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "beneficiaries",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("bank_account_reference", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "reference"),
    )
    op.create_table(
        "payouts",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("beneficiary_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("review_reason", sa.String(64), nullable=True),
        sa.Column("journal_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key_digest", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("response_http_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("response_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.CheckConstraint("amount > 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("status IN ('PROCESSING', 'REQUIRES_REVIEW', 'SUCCEEDED', 'FAILED')"),
        sa.CheckConstraint("octet_length(idempotency_key_digest) = 32"),
        sa.CheckConstraint("octet_length(fingerprint_sha256) = 32"),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND journal_id IS NOT NULL AND completed_at IS NOT NULL) OR "
            "(status <> 'SUCCEEDED' AND journal_id IS NULL)"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "beneficiary_id"],
            [
                "beneficiaries.organisation_id",
                "beneficiaries.environment_id",
                "beneficiaries.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
    )
    op.create_table(
        "payout_reservation_history",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("payout_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("amount_delta", sa.BigInteger(), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("sequence > 0"),
        sa.CheckConstraint("action IN ('RESERVED', 'RELEASED', 'CONSUMED')"),
        sa.CheckConstraint(
            "(action = 'RESERVED' AND amount_delta > 0) OR "
            "(action IN ('RELEASED', 'CONSUMED') AND amount_delta < 0)"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("payout_id", "sequence"),
    )
    op.create_index(
        "ix_payout_reservation_account",
        "payout_reservation_history",
        ["merchant_account_id", "created_at", "id"],
    )
    op.create_table(
        "payout_attempts",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("payout_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("stable_provider_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("request_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("review_reason", sa.String(64), nullable=True),
        sa.Column("idempotency_key_digest", sa.LargeBinary(), nullable=True),
        sa.Column("fingerprint_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("response_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.CheckConstraint("attempt_number > 0"),
        sa.CheckConstraint("status IN ('PREPARED', 'SENT', 'AMBIGUOUS', 'SUCCEEDED', 'FAILED')"),
        sa.CheckConstraint(
            "last_sent_at IS NULL OR (request_bytes IS NOT NULL AND request_sha256 IS NOT NULL)"
        ),
        sa.CheckConstraint(
            "(idempotency_key_digest IS NULL AND fingerprint_sha256 IS NULL) OR "
            "(octet_length(idempotency_key_digest) = 32 "
            "AND octet_length(fingerprint_sha256) = 32)"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("payout_id", "attempt_number"),
        sa.UniqueConstraint("organisation_id", "environment_id", "stable_provider_key"),
        sa.UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
    )
    op.create_index(
        "uq_payout_attempts_active",
        "payout_attempts",
        ["payout_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PREPARED', 'SENT', 'AMBIGUOUS')"),
    )
    op.create_table(
        "payout_attempt_evidence",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("payout_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("evidence_kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("request_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("response_http_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("response_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("bank_signature_valid", sa.Boolean(), nullable=True),
        sa.Column("classification", sa.String(64), nullable=True),
        sa.Column("safe_error_code", sa.String(64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("sequence > 0"),
        sa.CheckConstraint("evidence_kind IN ('MUTATION_SEND', 'MUTATION_RESULT', 'LOOKUP')"),
        sa.CheckConstraint(
            "state IN ('SENT', 'RESPONSE_RECEIVED', 'TRANSPORT_ERROR', 'VALIDATION_REJECTED')"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_attempt_id"],
            [
                "payout_attempts.organisation_id",
                "payout_attempts.environment_id",
                "payout_attempts.id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("payout_attempt_id", "sequence"),
    )
    op.create_index(
        "uq_payout_attempt_one_mutation",
        "payout_attempt_evidence",
        ["payout_attempt_id"],
        unique=True,
        postgresql_where=sa.text("evidence_kind = 'MUTATION_SEND'"),
    )
    op.create_table(
        "payout_history",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("payout_id", sa.Uuid(), nullable=False),
        sa.Column("payout_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "actor_type IN ('ADMIN', 'PAYOUT_WORKER', 'RECOVERY_WORKER', 'FINALIZER')"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_attempt_id"],
            [
                "payout_attempts.organisation_id",
                "payout_attempts.environment_id",
                "payout_attempts.id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
    )
    op.create_index(
        "ix_payout_history_payout_created",
        "payout_history",
        ["payout_id", "created_at", "id"],
    )
    op.create_table(
        "payout_events",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("payout_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("event_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("event_type = 'payout.succeeded.v1'"),
        sa.CheckConstraint("octet_length(event_sha256) = 32"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payout_id"],
            ["payouts.organisation_id", "payouts.environment_id", "payouts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("payout_id", "event_type"),
    )

    op.drop_constraint("journals_journal_type_check", "journals", type_="check")
    op.create_check_constraint(
        "journals_journal_type_check",
        "journals",
        "journal_type IN ('CAPTURE', 'REFUND', 'COMPENSATION', 'OPENING', 'SETTLEMENT', 'PAYOUT')",
    )
    op.execute(
        """
        ALTER TABLE balance_transactions
          DROP CONSTRAINT balance_transactions_transaction_type_check;
        ALTER TABLE balance_transactions
          ADD CONSTRAINT balance_transactions_transaction_type_check
          CHECK (transaction_type IN ('OPENING', 'CAPTURE', 'REFUND', 'SETTLEMENT', 'PAYOUT'));
        """
    )
    op.execute(
        """
        CREATE TRIGGER payout_reservation_history_immutable
          BEFORE UPDATE OR DELETE ON payout_reservation_history
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_balance_evidence_mutation();
        CREATE TRIGGER payout_attempt_evidence_immutable
          BEFORE UPDATE OR DELETE ON payout_attempt_evidence
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_balance_evidence_mutation();
        CREATE TRIGGER payout_history_immutable
          BEFORE UPDATE OR DELETE ON payout_history
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_balance_evidence_mutation();
        CREATE TRIGGER payout_events_immutable
          BEFORE UPDATE OR DELETE ON payout_events
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_balance_evidence_mutation();
        """
    )


def downgrade() -> None:
    op.drop_constraint("journals_journal_type_check", "journals", type_="check")
    op.create_check_constraint(
        "journals_journal_type_check",
        "journals",
        "journal_type IN ('CAPTURE', 'REFUND', 'COMPENSATION', 'OPENING', 'SETTLEMENT')",
    )
    op.execute(
        """
        ALTER TABLE balance_transactions
          DROP CONSTRAINT balance_transactions_transaction_type_check;
        ALTER TABLE balance_transactions
          ADD CONSTRAINT balance_transactions_transaction_type_check
          CHECK (transaction_type IN ('OPENING', 'CAPTURE', 'REFUND', 'SETTLEMENT'));
        """
    )
    op.drop_table("payout_events")
    op.drop_index("ix_payout_history_payout_created", table_name="payout_history")
    op.drop_table("payout_history")
    op.drop_index("uq_payout_attempt_one_mutation", table_name="payout_attempt_evidence")
    op.drop_table("payout_attempt_evidence")
    op.drop_index("uq_payout_attempts_active", table_name="payout_attempts")
    op.drop_table("payout_attempts")
    op.drop_index("ix_payout_reservation_account", table_name="payout_reservation_history")
    op.drop_table("payout_reservation_history")
    op.drop_table("payouts")
    op.drop_table("beneficiaries")
