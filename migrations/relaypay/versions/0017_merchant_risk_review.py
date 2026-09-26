"""merchant risk review snapshots findings scores escalations

Revision ID: 0017_merchant_risk_review
Revises: 0016_settlement_intelligence
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_merchant_risk_review"
down_revision: str | None = "0016_settlement_intelligence"
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
        "onboarding_snapshots",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("site_ref", sa.String(128), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("source_url", sa.String(512), nullable=False),
        *timestamps(),
        sa.CheckConstraint("octet_length(snapshot_sha256) = 32"),
        scope_foreign_key(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "site_ref", "snapshot_sha256"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_onboarding_snapshots_scope_created",
        "onboarding_snapshots",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.create_table(
        "risk_reviews",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("onboarding_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("score_version", sa.Integer(), nullable=False),
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
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("score_version > 0"),
        sa.CheckConstraint("status IN ('PENDING', 'COMPLETED', 'ESCALATED', 'DISPOSITIONED')"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["onboarding_snapshot_id"], ["onboarding_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organisation_id", "environment_id", "onboarding_snapshot_id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_risk_reviews_scope_created",
        "risk_reviews",
        ["organisation_id", "environment_id", "created_at"],
    )
    op.create_table(
        "risk_review_versions",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("risk_review_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("score_version", sa.Integer(), nullable=False),
        sa.Column("completeness_score", sa.Integer(), nullable=False),
        sa.Column("domain_score", sa.Integer(), nullable=False),
        sa.Column("category_score", sa.Integer(), nullable=False),
        sa.Column("pricing_score", sa.Integer(), nullable=False),
        sa.Column("claims_score", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("hard_stop", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "completeness_score BETWEEN 0 AND 20 AND domain_score BETWEEN 0 AND 20 "
            "AND category_score BETWEEN 0 AND 30 AND pricing_score BETWEEN 0 AND 15 "
            "AND claims_score BETWEEN 0 AND 15"
        ),
        sa.CheckConstraint(
            "total_score = completeness_score + domain_score + category_score "
            "+ pricing_score + claims_score"
        ),
        sa.CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"),
        sa.CheckConstraint("confidence IN ('HIGH', 'MEDIUM', 'LOW')"),
        sa.CheckConstraint("total_score BETWEEN 0 AND 100"),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("octet_length(review_sha256) = 32"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("risk_review_id", "version"),
    )
    op.create_table(
        "risk_findings",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("risk_review_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("finding_type", sa.String(16), nullable=False),
        sa.Column("check_key", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("source_path", sa.String(256), nullable=True),
        sa.Column("snapshot_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("hard_stop", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "classification IN ('NONE', 'SUSPICIOUS_LANGUAGE', 'UNREALISTIC_CLAIM', "
            "'GUARANTEED_RETURN', 'PROHIBITED_CATEGORY', 'IMPERSONATION', 'COUNTERFEIT')"
        ),
        sa.CheckConstraint("finding_type IN ('DETERMINISTIC', 'MODEL')"),
        sa.CheckConstraint("position > 0"),
        sa.CheckConstraint("severity IN ('NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"),
        sa.CheckConstraint("confidence IN ('HIGH', 'MEDIUM', 'LOW')"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("risk_review_id", "position"),
    )
    op.create_table(
        "risk_escalations",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("risk_review_id", sa.Uuid(), nullable=False),
        sa.Column("risk_review_version_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=True),
        sa.Column("disposition_note", sa.Text(), nullable=True),
        sa.Column("dispositioned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("dispositioned_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(updated=True),
        sa.CheckConstraint(
            "disposition IS NULL OR disposition IN "
            "('REJECT_ONBOARDING', 'REQUEST_DOCUMENTS', 'CLOSE_NO_ACTION')"
        ),
        sa.CheckConstraint(
            "reason IN ('HIGH_SEVERITY', 'CRITICAL_SEVERITY', 'PROHIBITED_CATEGORY', "
            "'IMPERSONATION', 'COUNTERFEIT', 'GUARANTEED_RETURN')"
        ),
        sa.CheckConstraint("status IN ('OPEN', 'DISPOSITIONED')"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        sa.ForeignKeyConstraint(["risk_review_version_id"], ["risk_review_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("risk_review_id"),
    )
    op.create_table(
        "risk_annotations",
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("risk_review_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        *timestamps(),
        sa.CheckConstraint("length(note) BETWEEN 1 AND 2000"),
        scope_foreign_key(),
        sa.ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_risk_annotations_scope_created",
        "risk_annotations",
        ["organisation_id", "environment_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_annotations_scope_created", table_name="risk_annotations")
    op.drop_table("risk_annotations")
    op.drop_table("risk_escalations")
    op.drop_table("risk_findings")
    op.drop_table("risk_review_versions")
    op.drop_index("ix_risk_reviews_scope_created", table_name="risk_reviews")
    op.drop_table("risk_reviews")
    op.drop_index("ix_onboarding_snapshots_scope_created", table_name="onboarding_snapshots")
    op.drop_table("onboarding_snapshots")
