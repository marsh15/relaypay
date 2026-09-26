import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


def scope_constraint() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organisation_id", "environment_id"],
        ["environments.organisation_id", "environments.id"],
    )


class OnboardingSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "onboarding_snapshots"
    __table_args__ = (
        scope_constraint(),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "site_ref", "snapshot_sha256"),
        CheckConstraint("octet_length(snapshot_sha256) = 32"),
        Index(
            "ix_onboarding_snapshots_scope_created",
            "organisation_id",
            "environment_id",
            "created_at",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    site_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)


class RiskReview(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "risk_reviews"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["onboarding_snapshot_id"], ["onboarding_snapshots.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "onboarding_snapshot_id"),
        CheckConstraint("status IN ('PENDING', 'COMPLETED', 'ESCALATED', 'DISPOSITIONED')"),
        CheckConstraint("score_version > 0"),
        Index("ix_risk_reviews_scope_created", "organisation_id", "environment_id", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    onboarding_snapshot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score_version: Mapped[int] = mapped_column(Integer, nullable=False)


class RiskReviewVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "risk_review_versions"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("risk_review_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("total_score BETWEEN 0 AND 100"),
        CheckConstraint(
            "completeness_score BETWEEN 0 AND 20 AND domain_score BETWEEN 0 AND 20 "
            "AND category_score BETWEEN 0 AND 30 AND pricing_score BETWEEN 0 AND 15 "
            "AND claims_score BETWEEN 0 AND 15"
        ),
        CheckConstraint(
            "total_score = completeness_score + domain_score + category_score "
            "+ pricing_score + claims_score"
        ),
        CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"),
        CheckConstraint("confidence IN ('HIGH', 'MEDIUM', 'LOW')"),
        CheckConstraint("octet_length(review_sha256) = 32"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    risk_review_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    score_version: Mapped[int] = mapped_column(Integer, nullable=False)
    completeness_score: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_score: Mapped[int] = mapped_column(Integer, nullable=False)
    category_score: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_score: Mapped[int] = mapped_column(Integer, nullable=False)
    claims_score: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    breakdown: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    review_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    hard_stop: Mapped[bool] = mapped_column(nullable=False, default=False)


class RiskFinding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "risk_findings"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("risk_review_id", "position"),
        CheckConstraint("position > 0"),
        CheckConstraint("finding_type IN ('DETERMINISTIC', 'MODEL')"),
        CheckConstraint(
            "classification IN ('NONE', 'SUSPICIOUS_LANGUAGE', 'UNREALISTIC_CLAIM', "
            "'GUARANTEED_RETURN', 'PROHIBITED_CATEGORY', 'IMPERSONATION', 'COUNTERFEIT')"
        ),
        CheckConstraint("severity IN ('NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"),
        CheckConstraint("confidence IN ('HIGH', 'MEDIUM', 'LOW')"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    risk_review_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_type: Mapped[str] = mapped_column(String(16), nullable=False)
    check_key: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(String(256))
    snapshot_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    hard_stop: Mapped[bool] = mapped_column(nullable=False, default=False)


class RiskEscalation(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "risk_escalations"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        ForeignKeyConstraint(["risk_review_version_id"], ["risk_review_versions.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("risk_review_id"),
        CheckConstraint(
            "reason IN ('HIGH_SEVERITY', 'CRITICAL_SEVERITY', 'PROHIBITED_CATEGORY', "
            "'IMPERSONATION', 'COUNTERFEIT', 'GUARANTEED_RETURN')"
        ),
        CheckConstraint("status IN ('OPEN', 'DISPOSITIONED')"),
        CheckConstraint(
            "disposition IS NULL OR disposition IN "
            "('REJECT_ONBOARDING', 'REQUEST_DOCUMENTS', 'CLOSE_NO_ACTION')"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    risk_review_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    risk_review_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    disposition: Mapped[str | None] = mapped_column(String(32))
    disposition_note: Mapped[str | None] = mapped_column(Text)
    dispositioned_by_user_id: Mapped[uuid.UUID | None] = mapped_column()
    dispositioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RiskAnnotation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "risk_annotations"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["risk_review_id"], ["risk_reviews.id"]),
        UniqueConstraint("public_id"),
        CheckConstraint("length(note) BETWEEN 1 AND 2000"),
        Index(
            "ix_risk_annotations_scope_created", "organisation_id", "environment_id", "created_at"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    risk_review_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    author_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
