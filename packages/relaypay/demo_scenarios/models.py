import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.database import Base
from relaypay.model_mixins import CreatedAtMixin, UUIDPrimaryKeyMixin


class ScenarioRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "scenario_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organisation_id", "environment_id"],
            ["environments.organisation_id", "environments.id"],
        ),
        ForeignKeyConstraint(
            ["organisation_id", "payment_intent_id"],
            ["payment_intents.organisation_id", "payment_intents.id"],
        ),
        UniqueConstraint("organisation_id", "id"),
        UniqueConstraint("public_id"),
        UniqueConstraint("correlation_id"),
        CheckConstraint("scenario_type = 'LOST_CAPTURE_RESPONSE'"),
        CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'NEEDS_INSPECTION')"),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)"
        ),
        Index("ix_scenario_runs_tenant_created", "organisation_id", "created_at"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payment_intent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    scenario_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RUNNING")
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    assertions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
