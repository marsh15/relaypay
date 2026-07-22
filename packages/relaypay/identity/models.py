import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Organisation(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "organisations"
    __table_args__ = (
        UniqueConstraint("id", "public_id"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class Environment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "environment_type"),
        CheckConstraint("environment_type IN ('TEST', 'LIVE_LIKE')"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    organisation: Mapped[Organisation] = relationship()


class User(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email_normalized"),
        CheckConstraint("platform_role IN ('PLATFORM_ADMIN', 'STANDARD')"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
    )

    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    platform_role: Mapped[str] = mapped_column(String(24), nullable=False, default="STANDARD")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class OrganisationMembership(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "organisation_memberships"
    __table_args__ = (
        UniqueConstraint("organisation_id", "user_id"),
        CheckConstraint("role IN ('ORGANISATION_ADMIN', 'DEVELOPER', 'VIEWER')"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class SessionRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "user_id"],
            ["organisation_memberships.organisation_id", "organisation_memberships.user_id"],
        ),
        Index(
            "ix_sessions_active_expiry", "expires_at", postgresql_where=text("revoked_at IS NULL")
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_digest: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    csrf_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class APIKey(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        UniqueConstraint("organisation_id", "environment_id", "id"),
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')"),
        Index("ix_api_keys_environment_status", "organisation_id", "environment_id", "status"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class APIKeyVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "api_key_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id", "api_key_id"],
            ["api_keys.organisation_id", "api_keys.environment_id", "api_keys.id"],
        ),
        UniqueConstraint("api_key_id", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("status IN ('PENDING', 'ACTIVE', 'REVOKED')"),
        Index(
            "uq_api_key_versions_one_active",
            "api_key_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    api_key_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    public_prefix: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    secret_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        CheckConstraint("actor_type IN ('USER', 'API_KEY', 'SYSTEM', 'COMMAND')"),
        Index("ix_audit_records_scope_created", "organisation_id", "environment_id", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
