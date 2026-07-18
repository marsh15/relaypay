import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin

EVENT_TYPES = (
    "payment.authorized.v1",
    "payment.captured.v1",
    "refund.succeeded.v1",
)


class WebhookEndpoint(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        UniqueConstraint("organisation_id", "id"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class WebhookEndpointVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "webhook_endpoint_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "webhook_endpoint_id"],
            ["webhook_endpoints.organisation_id", "webhook_endpoints.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("webhook_endpoint_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("active_until IS NULL OR active_until > active_from"),
        CheckConstraint("cardinality(subscribed_event_types) > 0"),
        CheckConstraint(
            "subscribed_event_types <@ ARRAY['payment.authorized.v1', "
            "'payment.captured.v1', 'refund.succeeded.v1']::varchar[]"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    webhook_endpoint_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    subscribed_event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MerchantEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "merchant_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "provider_operation_id"],
            ["provider_operations.organisation_id", "provider_operations.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("provider_operation_id", "event_type"),
        CheckConstraint(
            "event_type IN ('payment.authorized.v1', 'payment.captured.v1', 'refund.succeeded.v1')"
        ),
        CheckConstraint("schema_version = 1"),
        Index(
            "ix_merchant_events_payment_occurred",
            "organisation_id",
            "payment_intent_id",
            "occurred_at",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1)
    event_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    event_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventRecipient(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "event_recipients"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "merchant_event_id"],
            ["merchant_events.organisation_id", "merchant_events.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "endpoint_version_id"],
            ["webhook_endpoint_versions.organisation_id", "webhook_endpoint_versions.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("merchant_event_id", "endpoint_version_id"),
        Index("ix_event_recipients_event_order", "merchant_event_id", "id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    merchant_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    endpoint_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class WebhookDelivery(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "event_recipient_id"],
            ["event_recipients.organisation_id", "event_recipients.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "replay_of_delivery_id"],
            ["webhook_deliveries.organisation_id", "webhook_deliveries.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        CheckConstraint(
            "status IN ('PENDING', 'DELIVERING', 'RETRY_WAIT', 'DELIVERED', 'DEAD_LETTER')"
        ),
        CheckConstraint("attempt_count >= 0"),
        CheckConstraint(
            "(status IN ('PENDING', 'RETRY_WAIT') AND lease_token IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NULL "
            "AND dead_lettered_at IS NULL) OR "
            "(status = 'DELIVERING' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND delivered_at IS NULL "
            "AND dead_lettered_at IS NULL) OR "
            "(status = 'DELIVERED' AND lease_token IS NULL AND lease_expires_at IS NULL "
            "AND delivered_at IS NOT NULL AND dead_lettered_at IS NULL) OR "
            "(status = 'DEAD_LETTER' AND lease_token IS NULL AND lease_expires_at IS NULL "
            "AND delivered_at IS NULL AND dead_lettered_at IS NOT NULL)"
        ),
        Index(
            "uq_webhook_deliveries_initial_recipient",
            "event_recipient_id",
            unique=True,
            postgresql_where=text("replay_of_delivery_id IS NULL"),
        ),
        Index("ix_webhook_deliveries_claim", "status", "next_attempt_at"),
        Index(
            "ix_webhook_deliveries_expired_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'DELIVERING'"),
        ),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_recipient_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    replay_of_delivery_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDeliveryAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "webhook_delivery_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "webhook_delivery_id"],
            ["webhook_deliveries.organisation_id", "webhook_deliveries.id"],
        ),
        UniqueConstraint("webhook_delivery_id", "sequence"),
        CheckConstraint("sequence > 0"),
        CheckConstraint("result IN ('ACKNOWLEDGED', 'RETRYABLE', 'PERMANENT', 'TRANSPORT_ERROR')"),
        CheckConstraint(
            "(result = 'TRANSPORT_ERROR' AND response_http_status IS NULL) OR "
            "(result <> 'TRANSPORT_ERROR' AND response_http_status IS NOT NULL)"
        ),
        Index(
            "ix_webhook_delivery_attempts_delivery_order",
            "webhook_delivery_id",
            "sequence",
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    webhook_delivery_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    lease_token: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_http_status: Mapped[int | None] = mapped_column(SmallInteger)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
