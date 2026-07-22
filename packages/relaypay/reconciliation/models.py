import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class StatementImport(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "statement_imports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "environment_id", "provider", "source_reference"),
        CheckConstraint("provider = 'PAYMENT_PROVIDER'"),
        CheckConstraint("source_format IN ('CSV', 'JSON')"),
        CheckConstraint("period_end > period_start"),
        CheckConstraint("octet_length(raw_bytes) BETWEEN 1 AND 1048576"),
        CheckConstraint("octet_length(raw_sha256) = 32"),
        Index(
            "ix_statement_imports_scope_period",
            "organisation_id",
            "environment_id",
            "period_start",
            "period_end",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    source_format: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    raw_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class StatementItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "statement_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_import_id"],
            [
                "statement_imports.organisation_id",
                "statement_imports.environment_id",
                "statement_imports.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("statement_import_id", "ordinal"),
        UniqueConstraint("statement_import_id", "provider_item_id"),
        CheckConstraint("ordinal > 0"),
        CheckConstraint("operation_kind IN ('AUTHORIZE', 'CAPTURE', 'REFUND')"),
        CheckConstraint("amount > 0"),
        CheckConstraint("currency = 'INR'"),
        CheckConstraint("provider_status IN ('PENDING', 'SUCCEEDED', 'DECLINED')"),
        Index("ix_statement_items_stable_key", "statement_import_id", "stable_key"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    statement_import_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationRun(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_import_id"],
            [
                "statement_imports.organisation_id",
                "statement_imports.environment_id",
                "statement_imports.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("statement_import_id", "algorithm_version"),
        CheckConstraint("algorithm_version > 0"),
        CheckConstraint("status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')"),
        CheckConstraint("attempt_count >= 0"),
        CheckConstraint(
            "(status = 'RUNNING' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'RUNNING' AND lease_token IS NULL AND lease_expires_at IS NULL)"
        ),
        CheckConstraint(
            "(status IN ('COMPLETED', 'FAILED') AND completed_at IS NOT NULL) OR "
            "(status IN ('PENDING', 'RUNNING') AND completed_at IS NULL)"
        ),
        Index("ix_reconciliation_runs_claim", "status", "lease_expires_at", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    statement_import_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    algorithm_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))


class ReconciliationMatch(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "reconciliation_matches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_run_id"],
            [
                "reconciliation_runs.organisation_id",
                "reconciliation_runs.environment_id",
                "reconciliation_runs.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_item_id"],
            [
                "statement_items.organisation_id",
                "statement_items.environment_id",
                "statement_items.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("reconciliation_run_id", "statement_item_id"),
        CheckConstraint("match_type IN ('EXACT', 'DECLINED_WITHOUT_JOURNAL')"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reconciliation_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    statement_item_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    match_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class ReconciliationMismatch(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "reconciliation_mismatches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_run_id"],
            [
                "reconciliation_runs.organisation_id",
                "reconciliation_runs.environment_id",
                "reconciliation_runs.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "statement_item_id"],
            [
                "statement_items.organisation_id",
                "statement_items.environment_id",
                "statement_items.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "provider_operation_id"],
            [
                "provider_operations.organisation_id",
                "provider_operations.environment_id",
                "provider_operations.id",
            ],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "compensating_journal_id"],
            ["journals.organisation_id", "journals.environment_id", "journals.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("reconciliation_run_id", "subject_key"),
        CheckConstraint(
            "mismatch_type IN ('MISSING_INTERNAL_TRANSACTION', 'MISSING_PROVIDER_TRANSACTION', "
            "'AMOUNT_MISMATCH', 'CURRENCY_MISMATCH', 'STATUS_MISMATCH', "
            "'DUPLICATE_PROVIDER_EFFECT', 'MISSING_INTERNAL_JOURNAL')"
        ),
        CheckConstraint("workflow_status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')"),
        CheckConstraint(
            "(workflow_status = 'RESOLVED' AND resolved_at IS NOT NULL) OR "
            "(workflow_status <> 'RESOLVED' AND resolved_at IS NULL)"
        ),
        Index(
            "ix_reconciliation_mismatches_scope_status",
            "organisation_id",
            "environment_id",
            "workflow_status",
            "created_at",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reconciliation_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subject_key: Mapped[str] = mapped_column(String(256), nullable=False)
    mismatch_type: Mapped[str] = mapped_column(String(40), nullable=False)
    statement_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    provider_operation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    workflow_status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    acknowledgement_note: Mapped[str | None] = mapped_column(String(1000))
    resolution_note: Mapped[str | None] = mapped_column(String(1000))
    compensating_journal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MismatchEvidenceVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mismatch_evidence_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_mismatch_id"],
            [
                "reconciliation_mismatches.organisation_id",
                "reconciliation_mismatches.environment_id",
                "reconciliation_mismatches.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("reconciliation_mismatch_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("octet_length(evidence_sha256) = 32"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reconciliation_mismatch_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    evidence_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class MismatchWorkflowHistory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "mismatch_workflow_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "reconciliation_mismatch_id"],
            [
                "reconciliation_mismatches.organisation_id",
                "reconciliation_mismatches.environment_id",
                "reconciliation_mismatches.id",
            ],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        CheckConstraint("to_status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')"),
        CheckConstraint("from_status IS NULL OR from_status IN ('OPEN', 'ACKNOWLEDGED')"),
        Index(
            "ix_mismatch_workflow_history_order",
            "reconciliation_mismatch_id",
            "created_at",
            "id",
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reconciliation_mismatch_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000))


IMMUTABLE_RECONCILIATION_TABLES = (
    StatementImport,
    StatementItem,
    ReconciliationMatch,
    MismatchEvidenceVersion,
    MismatchWorkflowHistory,
)
