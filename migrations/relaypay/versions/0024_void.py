"""Void: release an authorization hold at the provider before capture.

Revision ID: 0024_void
Revises: 0023_authorization_expiry
Create Date: 2026-10-20

A ``voids`` row is the resource of a ``VOID`` provider operation (stable key ``void:{payment}``),
exactly as ``captures`` is for ``CAPTURE``. A void never posts to the ledger: authorization holds
never reach it. The operation-kind CHECKs, the provider-operation resource binding trigger, and
reconciliation statement items learn the new kind.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_void"
down_revision: str | None = "0023_authorization_expiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESOURCE_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION relaypay_validate_provider_operation_resource() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  resource_exists boolean;
BEGIN
  IF NEW.resource_type = 'AUTHORIZATION' AND NEW.kind = 'AUTHORIZE' THEN
    SELECT EXISTS (
      SELECT 1 FROM authorizations r
      WHERE r.id = NEW.resource_id AND r.organisation_id = NEW.organisation_id
        AND r.payment_intent_id = NEW.payment_intent_id AND r.provider_operation_id = NEW.id
    ) INTO resource_exists;
  ELSIF NEW.resource_type = 'CAPTURE' AND NEW.kind = 'CAPTURE' THEN
    SELECT EXISTS (
      SELECT 1 FROM captures r
      WHERE r.id = NEW.resource_id AND r.organisation_id = NEW.organisation_id
        AND r.payment_intent_id = NEW.payment_intent_id AND r.provider_operation_id = NEW.id
    ) INTO resource_exists;
  ELSIF NEW.resource_type = 'REFUND' AND NEW.kind = 'REFUND' THEN
    SELECT EXISTS (
      SELECT 1 FROM refunds r
      WHERE r.id = NEW.resource_id AND r.organisation_id = NEW.organisation_id
        AND r.payment_intent_id = NEW.payment_intent_id AND r.provider_operation_id = NEW.id
    ) INTO resource_exists;
  ELSIF __VOID_BRANCH__ THEN
    SELECT EXISTS (
      SELECT 1 FROM voids r
      WHERE r.id = NEW.resource_id AND r.organisation_id = NEW.organisation_id
        AND r.payment_intent_id = NEW.payment_intent_id AND r.provider_operation_id = NEW.id
    ) INTO resource_exists;
  ELSE
    resource_exists := false;
  END IF;

  IF NOT resource_exists THEN
    RAISE EXCEPTION 'provider operation % resource binding is invalid', NEW.id
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$$;
"""


def _replace_check(table: str, name: str, check: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, check)


def upgrade() -> None:
    op.create_table(
        "voids",
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_id", sa.Uuid(), nullable=False),
        sa.Column("provider_operation_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="voids_amount_check"),
        sa.CheckConstraint("currency = 'INR'", name="voids_currency_check"),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'SUCCEEDED', 'FAILED', 'REQUIRES_REVIEW')",
            name="voids_status_check",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND voided_at IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'FAILED' AND voided_at IS NULL AND failure_code IS NOT NULL) OR "
            "(status IN ('PROCESSING', 'REQUIRES_REVIEW') AND voided_at IS NULL)",
            name="voids_status_fields_check",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payment_intent_id"],
            [
                "payment_intents.organisation_id",
                "payment_intents.environment_id",
                "payment_intents.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "authorization_id"],
            [
                "authorizations.organisation_id",
                "authorizations.environment_id",
                "authorizations.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("organisation_id", "payment_intent_id"),
        sa.UniqueConstraint("provider_operation_id"),
        sa.UniqueConstraint("public_id"),
    )
    _replace_check(
        "provider_operations",
        "provider_operations_kind_check",
        "kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND', 'VOID')",
    )
    _replace_check(
        "provider_operations",
        "provider_operations_resource_type_check",
        "resource_type IN ('AUTHORIZATION', 'CAPTURE', 'REFUND', 'VOID')",
    )
    _replace_check(
        "statement_items",
        "statement_items_operation_kind_check",
        "operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND', 'VOID')",
    )
    op.execute(
        RESOURCE_TRIGGER_FUNCTION.replace(
            "__VOID_BRANCH__", "NEW.resource_type = 'VOID' AND NEW.kind = 'VOID'"
        )
    )


def downgrade() -> None:
    op.execute(RESOURCE_TRIGGER_FUNCTION.replace("__VOID_BRANCH__", "false"))
    _replace_check(
        "statement_items",
        "statement_items_operation_kind_check",
        "operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')",
    )
    _replace_check(
        "provider_operations",
        "provider_operations_resource_type_check",
        "resource_type IN ('AUTHORIZATION', 'CAPTURE', 'REFUND')",
    )
    _replace_check(
        "provider_operations",
        "provider_operations_kind_check",
        "kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')",
    )
    op.drop_table("voids")
