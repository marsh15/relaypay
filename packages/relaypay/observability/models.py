import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class RequestLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A deliberately small request record; headers, cookies, and bodies are never stored."""

    __tablename__ = "request_logs"
    __table_args__ = (
        CheckConstraint("status_code BETWEEN 100 AND 599"),
        CheckConstraint("duration_ms >= 0"),
        Index("ix_request_logs_created", "created_at", "id"),
        Index(
            "ix_request_logs_scope_created",
            "organisation_id",
            "environment_id",
            "created_at",
        ),
    )

    request_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    route: Mapped[str] = mapped_column(String(256), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class UsageRollup(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    """Hourly low-cardinality request totals used for durable operational evidence."""

    __tablename__ = "usage_rollups"
    __table_args__ = (
        UniqueConstraint(
            "bucket_start",
            "scope_key",
            "method",
            "route",
            "status_class",
        ),
        CheckConstraint("request_count > 0"),
        CheckConstraint("duration_ms_total >= 0"),
        CheckConstraint("duration_ms_max >= 0"),
        Index("ix_usage_rollups_bucket", "bucket_start"),
    )

    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    route: Mapped[str] = mapped_column(String(256), nullable=False)
    status_class: Mapped[str] = mapped_column(String(3), nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ms_max: Mapped[int] = mapped_column(Integer, nullable=False)
