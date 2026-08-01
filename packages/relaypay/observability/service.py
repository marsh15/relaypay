import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from relaypay.observability.metrics import OperationsMetrics
from relaypay.observability.models import RequestLog, UsageRollup

logger = logging.getLogger("relaypay.operations")


def _hour_bucket(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def record_request(
    factory: sessionmaker[Session],
    *,
    request_id: str,
    organisation_id: uuid.UUID | None,
    environment_id: uuid.UUID | None,
    method: str,
    route: str,
    status_code: int,
    duration_ms: int,
    retention: int,
    recorded_at: datetime | None = None,
) -> None:
    """Persist bounded metadata and a durable rollup after the response is produced."""

    now = recorded_at or datetime.now(UTC)
    scope_key = f"{organisation_id or 'anonymous'}:{environment_id or 'none'}"
    status_class = f"{status_code // 100}xx"
    with factory() as session, session.begin():
        session.add(
            RequestLog(
                request_id=request_id[:96],
                organisation_id=organisation_id,
                environment_id=environment_id,
                method=method[:12],
                route=route[:256],
                status_code=status_code,
                duration_ms=max(0, duration_ms),
            )
        )
        statement = insert(UsageRollup).values(
            bucket_start=_hour_bucket(now),
            scope_key=scope_key,
            method=method[:12],
            route=route[:256],
            status_class=status_class,
            request_count=1,
            duration_ms_total=max(0, duration_ms),
            duration_ms_max=max(0, duration_ms),
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    UsageRollup.bucket_start,
                    UsageRollup.scope_key,
                    UsageRollup.method,
                    UsageRollup.route,
                    UsageRollup.status_class,
                ],
                set_={
                    "request_count": UsageRollup.request_count + 1,
                    "duration_ms_total": UsageRollup.duration_ms_total
                    + statement.excluded.duration_ms_total,
                    "duration_ms_max": func.greatest(
                        UsageRollup.duration_ms_max,
                        statement.excluded.duration_ms_max,
                    ),
                    "updated_at": now,
                },
            )
        )
        oldest_to_keep = (
            select(RequestLog.id)
            .order_by(RequestLog.created_at.desc(), RequestLog.id.desc())
            .offset(retention)
        )
        session.execute(delete(RequestLog).where(RequestLog.id.in_(oldest_to_keep)))


def emit_structured_audit(
    *,
    audit_id: str,
    action: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    organisation_id: uuid.UUID | None,
    environment_id: uuid.UUID | None,
    target_type: str,
    target_id: str,
) -> None:
    logger.info(
        "audit action=%s audit_id=%s actor_type=%s actor_id=%s organisation_id=%s "
        "environment_id=%s target_type=%s target_id=%s",
        action,
        audit_id,
        actor_type,
        actor_id,
        organisation_id,
        environment_id,
        target_type,
        target_id,
    )


def refresh_operational_gauges(session: Session, metrics: OperationsMetrics) -> None:
    """Refresh database-authoritative queue, mismatch, and circuit gauges."""

    from relaypay.connectors.models import Connector
    from relaypay.event_delivery.models import WebhookDelivery
    from relaypay.payouts.models import Payout
    from relaypay.provider_operations.models import ProviderOperation
    from relaypay.reconciliation.models import ReconciliationMismatch

    for status in ("OPEN", "ACKNOWLEDGED", "RESOLVED"):
        count = session.scalar(
            select(func.count())
            .select_from(ReconciliationMismatch)
            .where(ReconciliationMismatch.workflow_status == status)
        )
        metrics.mismatches.labels(status.lower()).set(count or 0)
    queue_queries = {
        "provider_recovery": select(func.count())
        .select_from(ProviderOperation)
        .where(ProviderOperation.status.in_(("PROCESSING", "REQUIRES_REVIEW"))),
        "webhook_delivery": select(func.count())
        .select_from(WebhookDelivery)
        .where(WebhookDelivery.status.in_(("PENDING", "RETRY_WAIT"))),
        "payout": select(func.count())
        .select_from(Payout)
        .where(Payout.status.in_(("PROCESSING", "REQUIRES_REVIEW"))),
    }
    for queue, query in queue_queries.items():
        metrics.claim_depth.labels(queue).set(session.scalar(query) or 0)
    connectors = list(session.execute(select(Connector.public_id, Connector.circuit_state)))
    for connector_id, current_state in connectors:
        for state in ("CLOSED", "OPEN", "HALF_OPEN"):
            metrics.circuit_state.labels(connector_id, state.lower()).set(
                1 if current_state == state else 0
            )
