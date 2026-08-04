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
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin

WORKFLOW_STATES = (
    "QUEUED",
    "RUNNING",
    "WAITING_UNTIL",
    "WAITING_FOR_APPROVAL",
    "REQUIRES_REVIEW",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "DEAD_LETTER",
)


class ScopedMixin:
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


def scope_constraint() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organisation_id", "environment_id"],
        ["environments.organisation_id", "environments.id"],
    )


class BusinessEventOutbox(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "business_event_outbox"
    __table_args__ = (
        scope_constraint(),
        UniqueConstraint("event_id"),
        CheckConstraint("schema_version > 0"),
        CheckConstraint("publish_attempts >= 0"),
        Index("ix_business_event_outbox_claim", "published_at", "next_attempt_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    partition_key: Mapped[str] = mapped_column(String(192), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConsumedBusinessEvent(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "consumed_business_events"
    __table_args__ = (
        scope_constraint(),
        UniqueConstraint("consumer_name", "event_id"),
        Index("ix_consumed_business_events_scope", "organisation_id", "environment_id"),
    )
    consumer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class WorkflowDefinition(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        scope_constraint(),
        UniqueConstraint("organisation_id", "environment_id", "name", "version"),
        UniqueConstraint("public_id"),
        CheckConstraint("version > 0"),
        CheckConstraint("status IN ('ACTIVE', 'RETIRED')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class WorkflowRun(ScopedMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["workflow_definition_id"], ["workflow_definitions.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "route", "idempotency_digest"),
        CheckConstraint(f"status IN {WORKFLOW_STATES!r}"),
        CheckConstraint("token_budget >= 0 AND cost_budget_usd_micros >= 0"),
        Index("ix_workflow_runs_scope_created", "organisation_id", "environment_id", "created_at"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    trigger_event_id: Mapped[str | None] = mapped_column(String(64))
    route: Mapped[str] = mapped_column(String(192), nullable=False)
    idempotency_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_budget_usd_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_used_usd_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowStep(ScopedMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("workflow_run_id", "step_key"),
        CheckConstraint(f"status IN {WORKFLOW_STATES!r}"),
        CheckConstraint("attempt_count >= 0 AND max_attempts > 0"),
        CheckConstraint("step_kind IN ('TOOL', 'MODEL', 'APPROVAL', 'SYSTEM')"),
        Index("ix_workflow_steps_claim", "status", "next_attempt_at", "lease_expires_at"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    step_key: Mapped[str] = mapped_column(String(128), nullable=False)
    step_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowArtifact(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "workflow_artifacts"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("workflow_run_id", "artifact_type", "version"),
        CheckConstraint("version > 0 AND byte_length BETWEEN 0 AND 5242880"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class ApprovalRequest(ScopedMixin, UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        ForeignKeyConstraint(["artifact_id"], ["workflow_artifacts.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("workflow_run_id", "artifact_id", "artifact_sha256"),
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'INVALIDATED')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    artifact_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    maker_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class ApprovalDecision(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("approval_request_id"),
        CheckConstraint("decision IN ('APPROVED', 'REJECTED')"),
        CheckConstraint("decision_maker_user_id <> maker_user_id"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_request_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    maker_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    decision_maker_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class DeadLetter(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "workflow_dead_letters"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        ForeignKeyConstraint(["workflow_step_id"], ["workflow_steps.id"]),
        UniqueConstraint("public_id"),
        CheckConstraint("replay_count >= 0"),
        Index("ix_workflow_dead_letters_scope", "organisation_id", "environment_id", "created_at"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromptVersion(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        scope_constraint(),
        UniqueConstraint("name", "version", "prompt_sha256"),
        UniqueConstraint("public_id"),
        CheckConstraint("version > 0"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)


class PricingVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "pricing_versions"
    __table_args__ = (
        UniqueConstraint("provider", "model_id", "version"),
        UniqueConstraint("public_id"),
        CheckConstraint(
            "version > 0 AND input_usd_micros_per_million >= 0 "
            "AND output_usd_micros_per_million >= 0"
        ),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_usd_micros_per_million: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_usd_micros_per_million: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ModelInvocation(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        ForeignKeyConstraint(["workflow_step_id"], ["workflow_steps.id"]),
        UniqueConstraint("public_id"),
        CheckConstraint(
            "latency_ms >= 0 AND input_tokens >= 0 AND output_tokens >= 0 AND cost_usd_micros >= 0"
        ),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_step_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finish_status: Mapped[str] = mapped_column(String(32), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)


class ToolInvocation(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        ForeignKeyConstraint(["workflow_step_id"], ["workflow_steps.id"]),
        UniqueConstraint("public_id"),
        CheckConstraint("latency_ms >= 0 AND result_bytes >= 0 AND result_bytes <= 262144"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_step_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    output_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    result_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)


class EvaluationDataset(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        scope_constraint(),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "name", "version"),
        CheckConstraint("version > 0 AND case_count >= 0"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    cases: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)


class EvaluationRun(ScopedMixin, UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["evaluation_dataset_id"], ["evaluation_datasets.id"]),
        ForeignKeyConstraint(["workflow_definition_id"], ["workflow_definitions.id"]),
        UniqueConstraint("public_id"),
        CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'FAILED')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    result_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
