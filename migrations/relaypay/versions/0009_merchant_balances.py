"""Merchant accounts, ledger-backed balances, and deterministic settlement.

Revision ID: 0009_merchant_balances
Revises: 0008_statement_currency
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_merchant_balances"
down_revision: str | None = "0008_statement_currency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchant_accounts",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "reference"),
    )
    op.create_index(
        "uq_merchant_accounts_default",
        "merchant_accounts",
        ["organisation_id", "environment_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND status = 'ACTIVE'"),
    )
    op.execute(
        """
        INSERT INTO merchant_accounts
          (id, public_id, organisation_id, environment_id, reference, name, currency,
           is_default, status)
        SELECT md5(e.id::text || chr(58) || 'DEFAULT_MERCHANT')::uuid,
               'mac_' || replace(md5(e.id::text || chr(58) || 'DEFAULT_MERCHANT')::uuid::text,
                                  '-', ''),
               e.organisation_id, e.id, 'default', 'Default merchant account', 'INR', true,
               'ACTIVE'
          FROM environments e
        """
    )

    op.add_column("payment_intents", sa.Column("merchant_account_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE payment_intents p
           SET merchant_account_id = m.id
          FROM merchant_accounts m
         WHERE m.organisation_id = p.organisation_id
           AND m.environment_id = p.environment_id
           AND m.is_default
           AND m.status = 'ACTIVE'
        """
    )
    op.alter_column("payment_intents", "merchant_account_id", nullable=False)
    op.create_foreign_key(
        "payment_intents_merchant_account_fkey",
        "payment_intents",
        "merchant_accounts",
        ["organisation_id", "environment_id", "merchant_account_id"],
        ["organisation_id", "environment_id", "id"],
    )

    op.add_column("ledger_accounts", sa.Column("merchant_account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "ledger_accounts_merchant_account_fkey",
        "ledger_accounts",
        "merchant_accounts",
        ["organisation_id", "environment_id", "merchant_account_id"],
        ["organisation_id", "environment_id", "id"],
    )
    op.drop_constraint("ledger_accounts_scope_code_key", "ledger_accounts", type_="unique")
    op.create_index(
        "uq_ledger_accounts_global_code",
        "ledger_accounts",
        ["organisation_id", "environment_id", "code", "currency"],
        unique=True,
        postgresql_where=sa.text("merchant_account_id IS NULL"),
    )
    op.create_index(
        "uq_ledger_accounts_merchant_code",
        "ledger_accounts",
        ["organisation_id", "environment_id", "merchant_account_id", "code", "currency"],
        unique=True,
        postgresql_where=sa.text("merchant_account_id IS NOT NULL"),
    )
    op.execute(
        """
        INSERT INTO ledger_accounts
          (id, organisation_id, environment_id, merchant_account_id, code, name, account_type,
           currency)
        SELECT md5(m.id::text || chr(58) || template.code)::uuid,
               m.organisation_id, m.environment_id, m.id, template.code, template.name,
               template.account_type, 'INR'
          FROM merchant_accounts m
          CROSS JOIN (VALUES
            ('PENDING_PAYABLE_LIABILITY', 'Pending merchant payable', 'LIABILITY'),
            ('AVAILABLE_PAYABLE_LIABILITY', 'Available merchant payable', 'LIABILITY'),
            ('PAYOUT_CLEARING_ASSET', 'Payout clearing', 'ASSET'),
            ('MERCHANT_RECEIVABLE_ASSET', 'Merchant receivable', 'ASSET')
          ) AS template(code, name, account_type)
        """
    )

    op.add_column("journals", sa.Column("merchant_account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "journals_merchant_account_fkey",
        "journals",
        "merchant_accounts",
        ["organisation_id", "environment_id", "merchant_account_id"],
        ["organisation_id", "environment_id", "id"],
    )
    op.alter_column("journals", "provider_operation_id", nullable=True)
    op.drop_constraint("journals_journal_type_check", "journals", type_="check")
    op.create_check_constraint(
        "journals_journal_type_check",
        "journals",
        "journal_type IN ('CAPTURE', 'REFUND', 'COMPENSATION', 'OPENING', 'SETTLEMENT')",
    )

    op.create_table(
        "settlement_runs",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_digest", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("settled_amount", sa.BigInteger(), nullable=False),
        sa.Column("response_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("response_sha256", sa.LargeBinary(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('PROCESSING', 'COMPLETED', 'FAILED')"),
        sa.CheckConstraint("settled_amount >= 0"),
        sa.CheckConstraint("octet_length(idempotency_key_digest) = 32"),
        sa.CheckConstraint("octet_length(fingerprint_sha256) = 32"),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL "
            "AND response_bytes IS NOT NULL AND response_sha256 IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL "
            "AND response_bytes IS NULL AND response_sha256 IS NULL)"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "merchant_account_id"],
            [
                "merchant_accounts.organisation_id",
                "merchant_accounts.environment_id",
                "merchant_accounts.id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "idempotency_key_digest"),
    )
    op.create_table(
        "settlement_items",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("settlement_run_id", sa.Uuid(), nullable=False),
        sa.Column("capture_id", sa.Uuid(), nullable=False),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "settlement_run_id"],
            [
                "settlement_runs.organisation_id",
                "settlement_runs.environment_id",
                "settlement_runs.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "capture_id"],
            ["captures.organisation_id", "captures.environment_id", "captures.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("capture_id"),
        sa.UniqueConstraint("journal_id"),
    )
    op.create_table(
        "balance_transactions",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_type", sa.String(16), nullable=False),
        sa.Column("pending_delta", sa.BigInteger(), nullable=False),
        sa.Column("available_delta", sa.BigInteger(), nullable=False),
        sa.Column("receivable_delta", sa.BigInteger(), nullable=False),
        sa.Column("payout_clearing_delta", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("transaction_type IN ('OPENING', 'CAPTURE', 'REFUND', 'SETTLEMENT')"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint(
            "pending_delta <> 0 OR available_delta <> 0 OR receivable_delta <> 0 "
            "OR payout_clearing_delta <> 0"
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
            ["organisation_id", "environment_id", "journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("journal_id"),
    )
    op.create_index(
        "ix_balance_transactions_account_created",
        "balance_transactions",
        ["merchant_account_id", "created_at", "id"],
    )

    op.execute(
        """
        WITH legacy_positions AS (
          SELECT a.organisation_id, a.environment_id,
                 sum(CASE WHEN p.side = 'CREDIT' THEN p.amount ELSE -p.amount END)::bigint amount
            FROM ledger_accounts a
            JOIN postings p ON p.account_id = a.id
           WHERE a.code = 'MERCHANT_PAYABLE_LIABILITY'
             AND a.merchant_account_id IS NULL
           GROUP BY a.organisation_id, a.environment_id
          HAVING sum(CASE WHEN p.side = 'CREDIT' THEN p.amount ELSE -p.amount END) > 0
        ), opening AS (
          INSERT INTO journals
            (id, public_id, organisation_id, environment_id, merchant_account_id,
             provider_operation_id, journal_type, reference_type, reference_id, currency)
          SELECT md5(m.id::text || chr(58) || 'PHASE2_OPENING')::uuid,
                 'jrn_open_' || replace(m.id::text, '-', ''), m.organisation_id,
                 m.environment_id, m.id, NULL, 'OPENING', 'MERCHANT_ACCOUNT', m.id, 'INR'
            FROM merchant_accounts m
            JOIN legacy_positions l ON l.organisation_id = m.organisation_id
                                   AND l.environment_id = m.environment_id
           WHERE m.is_default AND m.status = 'ACTIVE'
          RETURNING id, organisation_id, environment_id, merchant_account_id
        )
        INSERT INTO postings
          (id, organisation_id, environment_id, journal_id, account_id, side, amount, currency)
        SELECT md5(o.id::text || chr(58) || side.name)::uuid, o.organisation_id,
               o.environment_id, o.id,
               CASE WHEN side.name = 'DEBIT' THEN legacy.id ELSE pending.id END,
               side.name, l.amount, 'INR'
          FROM opening o
          JOIN legacy_positions l ON l.organisation_id = o.organisation_id
                                 AND l.environment_id = o.environment_id
          JOIN ledger_accounts legacy ON legacy.organisation_id = o.organisation_id
                                     AND legacy.environment_id = o.environment_id
                                     AND legacy.code = 'MERCHANT_PAYABLE_LIABILITY'
                                     AND legacy.merchant_account_id IS NULL
          JOIN ledger_accounts pending ON pending.merchant_account_id = o.merchant_account_id
                                      AND pending.code = 'PENDING_PAYABLE_LIABILITY'
          CROSS JOIN (VALUES ('DEBIT'), ('CREDIT')) AS side(name)
        """
    )
    op.execute(
        """
        INSERT INTO balance_transactions
          (id, public_id, organisation_id, environment_id, merchant_account_id, journal_id,
           transaction_type, pending_delta, available_delta, receivable_delta,
           payout_clearing_delta, currency)
        SELECT md5(j.id::text || chr(58) || 'BALANCE')::uuid,
               'btx_' || replace(md5(j.id::text || chr(58) || 'BALANCE')::uuid::text, '-', ''),
               j.organisation_id, j.environment_id, j.merchant_account_id, j.id, 'OPENING',
               p.amount, 0, 0, 0, 'INR'
          FROM journals j
          JOIN postings p ON p.journal_id = j.id AND p.side = 'CREDIT'
          JOIN ledger_accounts a ON a.id = p.account_id
         WHERE j.journal_type = 'OPENING' AND a.code = 'PENDING_PAYABLE_LIABILITY'
        """
    )
    op.execute(
        """
        CREATE FUNCTION relaypay_prevent_balance_evidence_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$;
        CREATE TRIGGER settlement_items_immutable
          BEFORE UPDATE OR DELETE ON settlement_items
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_balance_evidence_mutation();
        CREATE TRIGGER balance_transactions_immutable
          BEFORE UPDATE OR DELETE ON balance_transactions
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_balance_evidence_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS relaypay_prevent_balance_evidence_mutation() CASCADE")
    op.drop_index("ix_balance_transactions_account_created", table_name="balance_transactions")
    op.drop_table("balance_transactions")
    op.drop_table("settlement_items")
    op.drop_table("settlement_runs")
    op.execute(
        """
        ALTER TABLE postings DISABLE TRIGGER postings_immutable;
        ALTER TABLE journals DISABLE TRIGGER journals_immutable;
        DELETE FROM postings
         WHERE journal_id IN (SELECT id FROM journals WHERE journal_type = 'OPENING');
        DELETE FROM journals WHERE journal_type = 'OPENING';
        ALTER TABLE postings ENABLE TRIGGER postings_immutable;
        ALTER TABLE journals ENABLE TRIGGER journals_immutable;
        """
    )
    op.drop_constraint("journals_journal_type_check", "journals", type_="check")
    op.create_check_constraint(
        "journals_journal_type_check",
        "journals",
        "journal_type IN ('CAPTURE', 'REFUND', 'COMPENSATION')",
    )
    op.drop_constraint("journals_merchant_account_fkey", "journals", type_="foreignkey")
    op.drop_column("journals", "merchant_account_id")
    op.alter_column("journals", "provider_operation_id", nullable=False)
    op.drop_index("uq_ledger_accounts_merchant_code", table_name="ledger_accounts")
    op.drop_index("uq_ledger_accounts_global_code", table_name="ledger_accounts")
    op.execute("DELETE FROM ledger_accounts WHERE merchant_account_id IS NOT NULL")
    op.drop_constraint(
        "ledger_accounts_merchant_account_fkey", "ledger_accounts", type_="foreignkey"
    )
    op.drop_column("ledger_accounts", "merchant_account_id")
    op.create_unique_constraint(
        "ledger_accounts_scope_code_key",
        "ledger_accounts",
        ["organisation_id", "environment_id", "code", "currency"],
    )
    op.drop_constraint(
        "payment_intents_merchant_account_fkey", "payment_intents", type_="foreignkey"
    )
    op.drop_column("payment_intents", "merchant_account_id")
    op.drop_index("uq_merchant_accounts_default", table_name="merchant_accounts")
    op.drop_table("merchant_accounts")
