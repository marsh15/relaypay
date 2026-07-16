import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UpdatedAtMixin, UUIDPrimaryKeyMixin


class Organisation(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "organisations"
    __table_args__ = (CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),)

    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class User(UUIDPrimaryKeyMixin, UpdatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("organisation_id", "email_normalized"),
        CheckConstraint("role = 'ADMIN'"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="ADMIN")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class SessionRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(["organisation_id", "user_id"], ["users.organisation_id", "users.id"]),
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
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')"),
        Index("ix_api_keys_organisation_status", "organisation_id", "status"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    public_prefix: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    secret_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
