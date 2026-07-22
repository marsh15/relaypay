"""Global identity, isolated environments, and versioned API keys.

Revision ID: 0006_identity_environments
Revises: 0005_scenarios
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_identity_environments"
down_revision: str | None = "0005_scenarios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENVIRONMENT_TABLES = (
    "customers",
    "payment_intents",
    "ledger_accounts",
    "provider_operations",
    "provider_attempts",
    "operation_history",
    "idempotency_records",
    "authorizations",
    "captures",
    "refunds",
    "journals",
    "postings",
    "webhook_endpoints",
    "webhook_endpoint_versions",
    "merchant_events",
    "event_recipients",
    "webhook_deliveries",
    "webhook_delivery_attempts",
    "scenario_runs",
)

SCOPE_PARENT_TABLES = (
    "authorizations",
    "captures",
    "refunds",
    "provider_operations",
    "provider_attempts",
    "operation_history",
    "idempotency_records",
    "journals",
    "postings",
    "webhook_endpoint_versions",
    "merchant_events",
    "event_recipients",
    "webhook_deliveries",
    "webhook_delivery_attempts",
    "scenario_runs",
)

SCOPE_RELATIONSHIPS = (
    ("payment_intents", "customers", "customer_id"),
    ("provider_operations", "payment_intents", "payment_intent_id"),
    ("provider_attempts", "provider_operations", "provider_operation_id"),
    ("operation_history", "provider_operations", "provider_operation_id"),
    ("operation_history", "provider_attempts", "evidence_attempt_id"),
    ("idempotency_records", "provider_operations", "provider_operation_id"),
    ("authorizations", "payment_intents", "payment_intent_id"),
    ("authorizations", "provider_operations", "provider_operation_id"),
    ("captures", "payment_intents", "payment_intent_id"),
    ("captures", "authorizations", "authorization_id"),
    ("captures", "provider_operations", "provider_operation_id"),
    ("captures", "journals", "journal_id"),
    ("refunds", "payment_intents", "payment_intent_id"),
    ("refunds", "captures", "capture_id"),
    ("refunds", "provider_operations", "provider_operation_id"),
    ("refunds", "journals", "journal_id"),
    ("journals", "provider_operations", "provider_operation_id"),
    ("postings", "journals", "journal_id"),
    ("postings", "ledger_accounts", "account_id"),
    ("webhook_endpoint_versions", "webhook_endpoints", "webhook_endpoint_id"),
    ("merchant_events", "payment_intents", "payment_intent_id"),
    ("merchant_events", "provider_operations", "provider_operation_id"),
    ("event_recipients", "merchant_events", "merchant_event_id"),
    ("event_recipients", "webhook_endpoint_versions", "endpoint_version_id"),
    ("webhook_deliveries", "event_recipients", "event_recipient_id"),
    ("webhook_deliveries", "webhook_deliveries", "replay_of_delivery_id"),
    ("webhook_delivery_attempts", "webhook_deliveries", "webhook_delivery_id"),
    ("scenario_runs", "payment_intents", "payment_intent_id"),
)


def upgrade() -> None:
    op.create_unique_constraint(
        "organisations_id_public_id_key", "organisations", ["id", "public_id"]
    )
    op.create_table(
        "environments",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("environment_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("environment_type IN ('TEST', 'LIVE_LIKE')"),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("organisation_id", "id"),
        sa.UniqueConstraint("organisation_id", "environment_type"),
    )
    op.execute(
        """
        INSERT INTO environments (id, public_id, organisation_id, name, environment_type, status)
        SELECT md5(id::text || chr(58) || 'TEST')::uuid,
               'env_test_' || replace(id::text, '-', ''), id, 'Test', 'TEST', 'ACTIVE'
          FROM organisations
        UNION ALL
        SELECT md5(id::text || chr(58) || 'LIVE_LIKE')::uuid,
               'env_live_' || replace(id::text, '-', ''), id, 'Live-like', 'LIVE_LIKE', 'ACTIVE'
          FROM organisations
        """
    )

    op.create_table(
        "organisation_memberships",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('ORGANISATION_ADMIN', 'DEVELOPER', 'VIEWER')"),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "user_id"),
    )
    op.execute(
        """
        INSERT INTO organisation_memberships (id, organisation_id, user_id, role, status)
        SELECT md5(id::text || chr(58) || 'MEMBERSHIP')::uuid, organisation_id, id,
               'ORGANISATION_ADMIN', status
          FROM users
        """
    )

    op.drop_constraint("sessions_organisation_id_user_id_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "sessions_membership_fkey",
        "sessions",
        "organisation_memberships",
        ["organisation_id", "user_id"],
        ["organisation_id", "user_id"],
    )
    op.add_column(
        "users",
        sa.Column("platform_role", sa.String(24), server_default="STANDARD", nullable=False),
    )
    op.create_check_constraint(
        "users_platform_role_check", "users", "platform_role IN ('PLATFORM_ADMIN', 'STANDARD')"
    )
    op.create_unique_constraint("users_email_normalized_key", "users", ["email_normalized"])
    op.drop_constraint("users_organisation_id_email_normalized_key", "users", type_="unique")
    op.drop_constraint("users_organisation_id_id_key", "users", type_="unique")
    op.drop_constraint("users_role_check", "users", type_="check")
    op.drop_constraint("users_organisation_id_fkey", "users", type_="foreignkey")
    op.drop_column("users", "role")
    op.drop_column("users", "organisation_id")

    op.add_column("api_keys", sa.Column("public_id", sa.String(64), nullable=True))
    op.add_column("api_keys", sa.Column("environment_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE api_keys k
           SET public_id = 'key_' || replace(k.id::text, '-', ''),
               environment_id = e.id
          FROM environments e
         WHERE e.organisation_id = k.organisation_id AND e.environment_type = 'TEST'
        """
    )
    op.alter_column("api_keys", "public_id", nullable=False)
    op.alter_column("api_keys", "environment_id", nullable=False)
    op.create_unique_constraint("api_keys_public_id_key", "api_keys", ["public_id"])
    op.create_unique_constraint(
        "api_keys_scope_id_key", "api_keys", ["organisation_id", "environment_id", "id"]
    )
    op.create_foreign_key(
        "api_keys_environment_fkey",
        "api_keys",
        "environments",
        ["organisation_id", "environment_id"],
        ["organisation_id", "id"],
    )
    op.create_table(
        "api_key_versions",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("public_prefix", sa.String(32), nullable=False),
        sa.Column("secret_digest", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("status IN ('PENDING', 'ACTIVE', 'REVOKED')"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "api_key_id"],
            ["api_keys.organisation_id", "api_keys.environment_id", "api_keys.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_id", "version"),
        sa.UniqueConstraint("public_prefix"),
    )
    op.execute(
        """
        INSERT INTO api_key_versions
          (id, organisation_id, environment_id, api_key_id, version, public_prefix,
           secret_digest, status, activated_at, revoked_at, last_used_at)
        SELECT md5(id::text || chr(58) || 'VERSION' || chr(58) || '1')::uuid,
               organisation_id, environment_id, id, 1,
               public_prefix, secret_digest, status, created_at, revoked_at, last_used_at
          FROM api_keys
        """
    )
    op.create_index(
        "uq_api_key_versions_one_active",
        "api_key_versions",
        ["api_key_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index("ix_api_keys_organisation_status", table_name="api_keys")
    op.create_index(
        "ix_api_keys_environment_status",
        "api_keys",
        ["organisation_id", "environment_id", "status"],
    )
    op.drop_column("api_keys", "public_prefix")
    op.drop_column("api_keys", "secret_digest")
    op.drop_column("api_keys", "last_used_at")

    for table in ENVIRONMENT_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
    for table in ENVIRONMENT_TABLES:
        op.add_column(table, sa.Column("environment_id", sa.Uuid(), nullable=True))
        backfill_sql = f"""
            UPDATE {table} t
               SET environment_id = e.id
              FROM environments e
             WHERE e.organisation_id = t.organisation_id AND e.environment_type = 'TEST'
            """  # noqa: S608 - table names are fixed migration constants
        op.execute(backfill_sql)
        op.alter_column(table, "environment_id", nullable=False)
        op.create_foreign_key(
            f"{table}_environment_fkey",
            table,
            "environments",
            ["organisation_id", "environment_id"],
            ["organisation_id", "id"],
        )
    for table in ENVIRONMENT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")

    op.create_unique_constraint(
        "customers_scope_id_key", "customers", ["organisation_id", "environment_id", "id"]
    )
    op.create_unique_constraint(
        "customers_scope_reference_key",
        "customers",
        ["organisation_id", "environment_id", "merchant_customer_reference"],
    )
    op.create_unique_constraint(
        "payment_intents_scope_id_key",
        "payment_intents",
        ["organisation_id", "environment_id", "id"],
    )
    op.create_unique_constraint(
        "payment_intents_scope_reference_key",
        "payment_intents",
        ["organisation_id", "environment_id", "merchant_reference"],
    )
    op.create_unique_constraint(
        "ledger_accounts_scope_id_key",
        "ledger_accounts",
        ["organisation_id", "environment_id", "id"],
    )
    op.create_unique_constraint(
        "ledger_accounts_scope_code_key",
        "ledger_accounts",
        ["organisation_id", "environment_id", "code", "currency"],
    )
    op.create_unique_constraint(
        "provider_operations_scope_stable_key",
        "provider_operations",
        ["organisation_id", "environment_id", "stable_provider_key"],
    )
    op.create_unique_constraint(
        "provider_operations_scope_resource_key",
        "provider_operations",
        ["organisation_id", "environment_id", "kind", "resource_id"],
    )
    op.create_unique_constraint(
        "idempotency_records_scope_digest_key",
        "idempotency_records",
        ["organisation_id", "environment_id", "key_digest"],
    )
    op.create_unique_constraint(
        "journals_scope_reference_key",
        "journals",
        ["organisation_id", "environment_id", "journal_type", "reference_id"],
    )
    for name, table in (
        ("customers_organisation_id_merchant_customer_reference_key", "customers"),
        ("payment_intents_organisation_id_merchant_reference_key", "payment_intents"),
        ("ledger_accounts_organisation_id_code_currency_key", "ledger_accounts"),
        ("provider_operations_organisation_id_stable_provider_key_key", "provider_operations"),
        ("provider_operations_organisation_id_kind_resource_id_key", "provider_operations"),
        ("idempotency_records_organisation_id_key_digest_key", "idempotency_records"),
        ("journals_organisation_id_journal_type_reference_id_key", "journals"),
    ):
        op.drop_constraint(name, table, type_="unique")
    op.drop_index("uq_refunds_merchant_reference", table_name="refunds")
    op.create_index(
        "uq_refunds_merchant_reference",
        "refunds",
        ["organisation_id", "environment_id", "merchant_refund_reference"],
        unique=True,
        postgresql_where=sa.text("merchant_refund_reference IS NOT NULL"),
    )
    op.create_unique_constraint(
        "webhook_endpoints_scope_id_key",
        "webhook_endpoints",
        ["organisation_id", "environment_id", "id"],
    )
    for table in SCOPE_PARENT_TABLES:
        op.create_unique_constraint(
            f"{table}_scope_id_key", table, ["organisation_id", "environment_id", "id"]
        )
    for source, target, reference_column in SCOPE_RELATIONSHIPS:
        provider_link = target == "provider_operations" and source in {
            "authorizations",
            "captures",
            "refunds",
        }
        op.create_foreign_key(
            f"{source}_{reference_column}_scope_fkey",
            source,
            target,
            ["organisation_id", "environment_id", reference_column],
            ["organisation_id", "environment_id", "id"],
            deferrable=True if provider_link else None,
            initially="DEFERRED" if provider_link else None,
        )

    op.create_table(
        "audit_records",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("actor_type IN ('USER', 'API_KEY', 'SYSTEM', 'COMMAND')"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_audit_records_scope_created",
        "audit_records",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.execute(
        """
        CREATE TRIGGER audit_records_immutable
          BEFORE UPDATE OR DELETE ON audit_records
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_records_immutable ON audit_records")
    op.drop_index("ix_audit_records_scope_created", table_name="audit_records")
    op.drop_table("audit_records")
    for source, _target, reference_column in reversed(SCOPE_RELATIONSHIPS):
        op.drop_constraint(f"{source}_{reference_column}_scope_fkey", source, type_="foreignkey")
    for table in reversed(SCOPE_PARENT_TABLES):
        op.drop_constraint(f"{table}_scope_id_key", table, type_="unique")
    op.drop_index("uq_refunds_merchant_reference", table_name="refunds")
    op.create_index(
        "uq_refunds_merchant_reference",
        "refunds",
        ["organisation_id", "merchant_refund_reference"],
        unique=True,
        postgresql_where=sa.text("merchant_refund_reference IS NOT NULL"),
    )
    for name, table, columns in (
        (
            "customers_organisation_id_merchant_customer_reference_key",
            "customers",
            ["organisation_id", "merchant_customer_reference"],
        ),
        (
            "payment_intents_organisation_id_merchant_reference_key",
            "payment_intents",
            ["organisation_id", "merchant_reference"],
        ),
        (
            "ledger_accounts_organisation_id_code_currency_key",
            "ledger_accounts",
            ["organisation_id", "code", "currency"],
        ),
        (
            "provider_operations_organisation_id_stable_provider_key_key",
            "provider_operations",
            ["organisation_id", "stable_provider_key"],
        ),
        (
            "provider_operations_organisation_id_kind_resource_id_key",
            "provider_operations",
            ["organisation_id", "kind", "resource_id"],
        ),
        (
            "idempotency_records_organisation_id_key_digest_key",
            "idempotency_records",
            ["organisation_id", "key_digest"],
        ),
        (
            "journals_organisation_id_journal_type_reference_id_key",
            "journals",
            ["organisation_id", "journal_type", "reference_id"],
        ),
    ):
        op.create_unique_constraint(name, table, columns)
    for name, table in (
        ("journals_scope_reference_key", "journals"),
        ("idempotency_records_scope_digest_key", "idempotency_records"),
        ("provider_operations_scope_resource_key", "provider_operations"),
        ("provider_operations_scope_stable_key", "provider_operations"),
    ):
        op.drop_constraint(name, table, type_="unique")
    for name, table in (
        ("webhook_endpoints_scope_id_key", "webhook_endpoints"),
        ("ledger_accounts_scope_code_key", "ledger_accounts"),
        ("ledger_accounts_scope_id_key", "ledger_accounts"),
        ("payment_intents_scope_reference_key", "payment_intents"),
        ("payment_intents_scope_id_key", "payment_intents"),
        ("customers_scope_reference_key", "customers"),
        ("customers_scope_id_key", "customers"),
    ):
        op.drop_constraint(name, table, type_="unique")
    for table in reversed(ENVIRONMENT_TABLES):
        op.drop_constraint(f"{table}_environment_fkey", table, type_="foreignkey")
        op.drop_column(table, "environment_id")
    op.add_column("api_keys", sa.Column("last_used_at", sa.DateTime(timezone=True)))
    op.add_column("api_keys", sa.Column("secret_digest", sa.LargeBinary(), nullable=True))
    op.add_column("api_keys", sa.Column("public_prefix", sa.String(32), nullable=True))
    op.execute(
        """
        UPDATE api_keys k SET public_prefix = v.public_prefix, secret_digest = v.secret_digest,
          last_used_at = v.last_used_at
        FROM api_key_versions v WHERE v.api_key_id = k.id AND v.status = 'ACTIVE'
        """
    )
    op.alter_column("api_keys", "public_prefix", nullable=False)
    op.alter_column("api_keys", "secret_digest", nullable=False)
    op.create_unique_constraint("api_keys_public_prefix_key", "api_keys", ["public_prefix"])
    op.drop_table("api_key_versions")
    op.drop_constraint("api_keys_environment_fkey", "api_keys", type_="foreignkey")
    op.drop_constraint("api_keys_scope_id_key", "api_keys", type_="unique")
    op.drop_constraint("api_keys_public_id_key", "api_keys", type_="unique")
    op.drop_index("ix_api_keys_environment_status", table_name="api_keys")
    op.create_index("ix_api_keys_organisation_status", "api_keys", ["organisation_id", "status"])
    op.drop_column("api_keys", "environment_id")
    op.drop_column("api_keys", "public_id")
    op.add_column("users", sa.Column("organisation_id", sa.Uuid(), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(16), server_default="ADMIN", nullable=False))
    op.execute(
        """
        UPDATE users u SET organisation_id = m.organisation_id
        FROM organisation_memberships m WHERE m.user_id = u.id
        """
    )
    op.alter_column("users", "organisation_id", nullable=False)
    op.create_foreign_key(
        "users_organisation_id_fkey", "users", "organisations", ["organisation_id"], ["id"]
    )
    op.create_unique_constraint(
        "users_organisation_id_email_normalized_key",
        "users",
        ["organisation_id", "email_normalized"],
    )
    op.create_unique_constraint("users_organisation_id_id_key", "users", ["organisation_id", "id"])
    op.create_check_constraint("users_role_check", "users", "role = 'ADMIN'")
    op.drop_constraint("users_email_normalized_key", "users", type_="unique")
    op.drop_constraint("users_platform_role_check", "users", type_="check")
    op.drop_column("users", "platform_role")
    op.drop_constraint("sessions_membership_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "sessions_organisation_id_user_id_fkey",
        "sessions",
        "users",
        ["organisation_id", "user_id"],
        ["organisation_id", "id"],
    )
    op.drop_table("organisation_memberships")
    op.drop_table("environments")
