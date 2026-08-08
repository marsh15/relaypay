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


class DisputeCase(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "dispute_cases"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["payment_intent_id"], ["payment_intents.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "network_dispute_id"),
        CheckConstraint(
            "reason_code IN ('FRAUD', 'PRODUCT_NOT_RECEIVED', 'NOT_AS_DESCRIBED', "
            "'DUPLICATE', 'CREDIT_NOT_PROCESSED', 'OTHER')"
        ),
        CheckConstraint(
            "status IN ('OPEN', 'DRAFTED', 'WAITING_FOR_APPROVAL', 'APPROVED', "
            "'SUBMITTING', 'SUBMITTED', 'REQUIRES_REVIEW')"
        ),
        CheckConstraint("amount > 0 AND currency = 'INR'"),
        Index("ix_dispute_cases_scope_created", "organisation_id", "environment_id", "created_at"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    network_dispute_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DisputeDraftVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "dispute_draft_versions"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["dispute_case_id"], ["dispute_cases.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("dispute_case_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("author_type IN ('AGENT', 'ANALYST')"),
        CheckConstraint("confidence IN ('LOW', 'MEDIUM', 'HIGH')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dispute_case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    selected_evidence: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    missing_evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class DisputePackageVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "dispute_package_versions"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["dispute_case_id"], ["dispute_cases.id"]),
        ForeignKeyConstraint(["draft_version_id"], ["dispute_draft_versions.id"]),
        ForeignKeyConstraint(["workflow_artifact_id"], ["workflow_artifacts.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("dispute_case_id", "version"),
        UniqueConstraint("package_sha256"),
        CheckConstraint("version > 0 AND byte_length BETWEEN 0 AND 20971520"),
        CheckConstraint("status IN ('FROZEN', 'APPROVED', 'INVALIDATED', 'SUBMITTED')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dispute_case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    draft_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_artifact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    package_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    package_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class DisputeSubmissionAttempt(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "dispute_submission_attempts"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["dispute_case_id"], ["dispute_cases.id"]),
        ForeignKeyConstraint(["package_version_id"], ["dispute_package_versions.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("dispute_case_id", "attempt_number"),
        UniqueConstraint("stable_key"),
        CheckConstraint("attempt_number > 0"),
        CheckConstraint("status IN ('SENT', 'AMBIGUOUS', 'SUCCEEDED', 'FAILED')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dispute_case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    package_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(192), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_code: Mapped[str | None] = mapped_column(String(64))
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
