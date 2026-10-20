"""Platform fee and GST on captures (decision D7): schedules, INCOME accounts, capture split.

Revision ID: 0025_fees_gst
Revises: 0024_void
Create Date: 2026-10-20

* ``fee_schedule_versions``: immutable, versioned per merchant account (MDR basis points, fixed
  paise, GST basis points, optional min/max, refund policy). A change appends a version.
  Protected by the 0001 immutability trigger.
* ``ledger_accounts.account_type`` gains ``INCOME``. The global PLATFORM_FEE_REVENUE (INCOME)
  and GST_PAYABLE_LIABILITY accounts are created on first use.
* ``captures`` records the split decided at initiation: ``fee_amount``, ``gst_amount`` and the
  schedule version. With no schedule, both are 0 and no fee lines post. Existing captures
  therefore keep a fee of 0, and nothing is rewritten.

Only the RETAIN refund policy exists: the merchant bears the full refund, and the fee and GST
are not reversed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_fees_gst"
down_revision: str | None = "0024_void"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fee_schedule_versions",
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_account_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mdr_bps", sa.Integer(), nullable=False),
        sa.Column("fixed_paise", sa.Integer(), nullable=False),
        sa.Column("gst_bps", sa.Integer(), nullable=False),
        sa.Column("min_fee_paise", sa.Integer(), nullable=True),
        sa.Column("max_fee_paise", sa.Integer(), nullable=True),
        sa.Column("refund_fee_policy", sa.String(length=24), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="fee_schedule_versions_version_check"),
        sa.CheckConstraint(
            "mdr_bps BETWEEN 0 AND 10000 AND gst_bps BETWEEN 0 AND 10000 AND fixed_paise >= 0",
            name="fee_schedule_versions_rates_check",
        ),
        sa.CheckConstraint(
            "(min_fee_paise IS NULL OR min_fee_paise >= 0) AND "
            "(max_fee_paise IS NULL OR max_fee_paise >= 0) AND "
            "(min_fee_paise IS NULL OR max_fee_paise IS NULL OR min_fee_paise <= max_fee_paise)",
            name="fee_schedule_versions_bounds_check",
        ),
        sa.CheckConstraint(
            "refund_fee_policy IN ('RETAIN')", name="fee_schedule_versions_refund_policy_check"
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
        sa.UniqueConstraint("merchant_account_id", "version"),
        sa.UniqueConstraint("organisation_id", "environment_id", "id"),
        sa.UniqueConstraint("public_id"),
    )
    op.execute(
        "CREATE TRIGGER fee_schedule_versions_immutable "
        "BEFORE UPDATE OR DELETE ON fee_schedule_versions "
        "FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation()"
    )
    op.drop_constraint("ledger_accounts_account_type_check", "ledger_accounts", type_="check")
    op.create_check_constraint(
        "ledger_accounts_account_type_check",
        "ledger_accounts",
        "account_type IN ('ASSET', 'LIABILITY', 'INCOME')",
    )
    op.add_column(
        "captures",
        sa.Column("fee_amount", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "captures",
        sa.Column("gst_amount", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("captures", sa.Column("fee_schedule_version_id", sa.Uuid(), nullable=True))
    op.alter_column("captures", "fee_amount", server_default=None)
    op.alter_column("captures", "gst_amount", server_default=None)
    op.create_foreign_key(
        "captures_fee_schedule_version_fkey",
        "captures",
        "fee_schedule_versions",
        ["organisation_id", "environment_id", "fee_schedule_version_id"],
        ["organisation_id", "environment_id", "id"],
    )
    op.create_check_constraint(
        "captures_fee_split_check",
        "captures",
        "fee_amount >= 0 AND gst_amount >= 0 AND fee_amount + gst_amount <= amount",
    )
    op.create_check_constraint(
        "captures_fee_schedule_check",
        "captures",
        "fee_schedule_version_id IS NOT NULL OR (fee_amount = 0 AND gst_amount = 0)",
    )


def downgrade() -> None:
    op.drop_constraint("captures_fee_schedule_check", "captures", type_="check")
    op.drop_constraint("captures_fee_split_check", "captures", type_="check")
    op.drop_constraint("captures_fee_schedule_version_fkey", "captures", type_="foreignkey")
    op.drop_column("captures", "fee_schedule_version_id")
    op.drop_column("captures", "gst_amount")
    op.drop_column("captures", "fee_amount")
    op.drop_constraint("ledger_accounts_account_type_check", "ledger_accounts", type_="check")
    op.create_check_constraint(
        "ledger_accounts_account_type_check",
        "ledger_accounts",
        "account_type IN ('ASSET', 'LIABILITY')",
    )
    op.execute("DROP TRIGGER fee_schedule_versions_immutable ON fee_schedule_versions")
    op.drop_table("fee_schedule_versions")
