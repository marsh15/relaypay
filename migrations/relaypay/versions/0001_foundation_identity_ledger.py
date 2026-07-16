"""Foundation identity, tenancy, payments, and immutable ledger.

Revision ID: 0001_foundation
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organisations",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
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
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_table(
        "users",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
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
        sa.CheckConstraint("role = 'ADMIN'"),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "email_normalized"),
        sa.UniqueConstraint("organisation_id", "id"),
    )
    op.create_table(
        "api_keys",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("public_prefix", sa.String(32), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'REVOKED')"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_prefix"),
    )
    op.create_index("ix_api_keys_organisation_status", "api_keys", ["organisation_id", "status"])
    op.create_table(
        "sessions",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(), nullable=False),
        sa.Column("csrf_digest", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "user_id"], ["users.organisation_id", "users.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index(
        "ix_sessions_active_expiry",
        "sessions",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "customers",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_customer_reference", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "id"),
        sa.UniqueConstraint("organisation_id", "merchant_customer_reference"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_table(
        "payment_intents",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_reference", sa.String(128), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
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
        sa.CheckConstraint("amount > 0"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "customer_id"], ["customers.organisation_id", "customers.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "id"),
        sa.UniqueConstraint("organisation_id", "merchant_reference"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_payment_intents_organisation_created",
        "payment_intents",
        ["organisation_id", "created_at"],
    )
    op.create_table(
        "ledger_accounts",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("account_type", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("account_type IN ('ASSET', 'LIABILITY')"),
        sa.CheckConstraint("currency = 'INR'"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "code", "currency"),
        sa.UniqueConstraint("organisation_id", "id"),
    )
    op.create_table(
        "journals",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("provider_operation_id", sa.Uuid(), nullable=False),
        sa.Column("journal_type", sa.String(16), nullable=False),
        sa.Column("reference_type", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "posted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency = 'INR'"),
        sa.CheckConstraint("journal_type IN ('CAPTURE', 'REFUND', 'COMPENSATION')"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "id"),
        sa.UniqueConstraint("organisation_id", "journal_type", "reference_id"),
        sa.UniqueConstraint("provider_operation_id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_table(
        "postings",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
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
        sa.CheckConstraint("side IN ('DEBIT', 'CREDIT')"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "account_id"],
            ["ledger_accounts.organisation_id", "ledger_accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "journal_id"], ["journals.organisation_id", "journals.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_postings_account_created",
        "postings",
        ["organisation_id", "account_id", "created_at", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION relaypay_prevent_evidence_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER journals_immutable
          BEFORE UPDATE OR DELETE ON journals
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation();
        CREATE TRIGGER postings_immutable
          BEFORE UPDATE OR DELETE ON postings
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation();

        CREATE FUNCTION relaypay_validate_journal_balance() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          target_id uuid;
          posting_count integer;
          debit_total bigint;
          credit_total bigint;
          wrong_currency_count integer;
        BEGIN
          IF TG_TABLE_NAME = 'postings' THEN
            target_id := NEW.journal_id;
          ELSE
            target_id := NEW.id;
          END IF;
          SELECT
            count(*),
            COALESCE(sum(amount) FILTER (WHERE side = 'DEBIT'), 0),
            COALESCE(sum(amount) FILTER (WHERE side = 'CREDIT'), 0),
            count(*) FILTER (WHERE currency <> (SELECT currency FROM journals WHERE id = target_id))
          INTO posting_count, debit_total, credit_total, wrong_currency_count
          FROM postings WHERE journal_id = target_id;

          IF posting_count < 2 THEN
            RAISE EXCEPTION 'journal % requires at least two postings', target_id USING ERRCODE = '23514';
          END IF;
          IF debit_total <> credit_total THEN
            RAISE EXCEPTION 'journal % is unbalanced', target_id USING ERRCODE = '23514';
          END IF;
          IF wrong_currency_count <> 0 THEN
            RAISE EXCEPTION 'journal % posting currency mismatch', target_id USING ERRCODE = '23514';
          END IF;
          RETURN NULL;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER journals_balance_at_commit
          AFTER INSERT ON journals DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION relaypay_validate_journal_balance();
        CREATE CONSTRAINT TRIGGER postings_balance_at_commit
          AFTER INSERT ON postings DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION relaypay_validate_journal_balance();
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS relaypay_validate_journal_balance() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS relaypay_prevent_evidence_mutation() CASCADE")
    op.drop_index("ix_postings_account_created", table_name="postings")
    op.drop_table("postings")
    op.drop_table("journals")
    op.drop_table("ledger_accounts")
    op.drop_index("ix_payment_intents_organisation_created", table_name="payment_intents")
    op.drop_table("payment_intents")
    op.drop_table("customers")
    op.drop_index("ix_sessions_active_expiry", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_api_keys_organisation_status", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("users")
    op.drop_table("organisations")
