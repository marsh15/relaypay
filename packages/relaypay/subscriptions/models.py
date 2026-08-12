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


class Subscription(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "external_id"),
        CheckConstraint("status IN ('ACTIVE', 'PAST_DUE', 'RECOVERED', 'CANCELLED', 'EXPIRED')"),
        CheckConstraint("amount > 0 AND currency = 'INR'"),
        Index("ix_subscriptions_scope_created", "organisation_id", "environment_id", "created_at"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    consent: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    consent_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class SubscriptionInvoice(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "subscription_invoices"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("subscription_id", "external_id"),
        CheckConstraint("status IN ('OPEN', 'FAILED', 'PAID', 'VOID')"),
        CheckConstraint("amount > 0 AND currency = 'INR'"),
        Index(
            "ix_subscription_invoices_scope_created",
            "organisation_id",
            "environment_id",
            "created_at",
        ),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subscription_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecurringPaymentAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "recurring_payment_attempts"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["invoice_id"], ["subscription_invoices.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("invoice_id", "attempt_number"),
        UniqueConstraint("provider_attempt_id"),
        CheckConstraint("attempt_number > 0"),
        CheckConstraint(
            "outcome IN ('VERIFIED_FAILED', 'VERIFIED_SUCCEEDED', 'TRANSPORT_UNKNOWN')"
        ),
        CheckConstraint(
            "failure_classification IS NULL OR failure_classification IN "
            "('RETRYABLE_SOFT_DECLINE', 'NON_RETRYABLE_HARD_DECLINE', "
            "'AUTHENTICATION_REQUIRED', 'EXPIRED_METHOD', 'TRANSPORT_UNKNOWN')"
        ),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_attempt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_classification: Mapped[str | None] = mapped_column(String(40))
    provider_code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    evidence_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RecoveryPolicyVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "recovery_policy_versions"
    __table_args__ = (
        scope_constraint(),
        UniqueConstraint("public_id"),
        UniqueConstraint("organisation_id", "environment_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("max_payment_retries BETWEEN 0 AND 3"),
        CheckConstraint("max_messages BETWEEN 0 AND 3"),
        CheckConstraint("window_days BETWEEN 1 AND 14"),
        CheckConstraint("status IN ('ACTIVE', 'RETIRED')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    max_payment_retries: Mapped[int] = mapped_column(Integer, nullable=False)
    max_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_windows_hours: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    message_windows_hours: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    policy_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class RecoveryCase(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        ForeignKeyConstraint(["invoice_id"], ["subscription_invoices.id"]),
        ForeignKeyConstraint(["trigger_attempt_id"], ["recurring_payment_attempts.id"]),
        ForeignKeyConstraint(["policy_version_id"], ["recovery_policy_versions.id"]),
        ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("invoice_id"),
        CheckConstraint(
            "status IN ('OPEN', 'SCHEDULED', 'RECOVERED', 'TERMINATED', 'EXPIRED', "
            "'REQUIRES_REVIEW')"
        ),
        CheckConstraint("payment_retry_count BETWEEN 0 AND 3"),
        CheckConstraint("message_count BETWEEN 0 AND 3"),
        Index("ix_recovery_cases_scope_created", "organisation_id", "environment_id", "created_at"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subscription_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    trigger_attempt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    payment_retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termination_reason: Mapped[str | None] = mapped_column(String(64))


class ScheduledRecoveryAction(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "scheduled_recovery_actions"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["recovery_case_id"], ["recovery_cases.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("recovery_case_id", "sequence"),
        UniqueConstraint("stable_key"),
        CheckConstraint("sequence > 0"),
        CheckConstraint("action_type IN ('PAYMENT_RETRY', 'MESSAGE')"),
        CheckConstraint("channel IS NULL OR channel IN ('EMAIL', 'WHATSAPP', 'IN_APP')"),
        CheckConstraint(
            "status IN ('SCHEDULED', 'EXECUTING', 'EXECUTED', 'AMBIGUOUS', 'CANCELLED', "
            "'SUPPRESSED')"
        ),
        Index("ix_scheduled_recovery_actions_due", "status", "scheduled_for", "created_at"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(16))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(192), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_code: Mapped[str | None] = mapped_column(String(64))
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)


class CommunicationRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "communication_records"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["recovery_case_id"], ["recovery_cases.id"]),
        ForeignKeyConstraint(["scheduled_action_id"], ["scheduled_recovery_actions.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("scheduled_action_id"),
        UniqueConstraint("stable_key"),
        CheckConstraint("channel IN ('EMAIL', 'WHATSAPP', 'IN_APP')"),
        CheckConstraint("status IN ('SENT', 'AMBIGUOUS', 'DELIVERED', 'FAILED')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scheduled_action_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    stable_key: Mapped[str] = mapped_column(String(192), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    tokenized_body: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_body: Mapped[str] = mapped_column(Text, nullable=False)
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_code: Mapped[str | None] = mapped_column(String(64))
    response_sha256: Mapped[bytes | None] = mapped_column(LargeBinary)


class RecoveryOptOut(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "recovery_opt_outs"
    __table_args__ = (
        scope_constraint(),
        ForeignKeyConstraint(["recovery_case_id"], ["recovery_cases.id"]),
        UniqueConstraint("public_id"),
        UniqueConstraint("source_event_id"),
        CheckConstraint("channel IN ('ALL', 'EMAIL', 'WHATSAPP', 'IN_APP')"),
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
