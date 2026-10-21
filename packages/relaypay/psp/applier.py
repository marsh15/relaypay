"""Apply inbox PSP events to the operations they name, as WEBHOOK provider evidence.

For each leased ``psp_events`` row:

1. Validate the embedded effect against the operation's expected identity (account, stable key,
   kind, reference, amount, currency). The envelope signature was verified at ingest; a
   mismatch is evidence of a broken provider, so the event goes to REVIEW and the operation is
   marked for review, exactly as a mismatched lookup is.
2. Ask the maintainer's ``decide`` what the event means for the operation's current state.
   Until it is implemented, the event is DEFERRED: polling stays the guarantee (ADR-002).
3. ``Apply``: record a WEBHOOK ``ProviderAttempt`` and finalize through the one finalizer. This
   is the only path that moves money. ``Ignore`` / ``Review`` change only the event.

A crash between recording the attempt and finalizing is safe: the lease lapses, and the event is
reprocessed. The recorded attempt is found by its digest (unique per operation, 0027) and
finalized again; the finalizer is a no-op for a terminal operation. A finalize failure counts
toward the operation's apply-failure review limit, as it does for lookups. An event that keeps
failing is dead-lettered after ``MAX_EVENT_ATTEMPTS`` claims, so it cannot stall the batch.
"""

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.provider_operations.finalizer import (
    ActorType,
    finalize_verified_attempt,
    mark_operation_for_review,
    record_apply_failure,
)
from relaypay.provider_operations.models import ProviderAttempt, ProviderOperation
from relaypay.provider_operations.service import expected_result_for
from relaypay.provider_operations.validation import ExpectedProviderResult, ProviderResult
from relaypay.psp.inbox import SimulatorEnvelope
from relaypay.psp.models import PspEvent
from relaypay.psp.ordering import decide
from relaypay.psp.types import (
    Apply,
    Decision,
    Ignore,
    NormalizedPspEvent,
    OperationStatus,
    OperationView,
)

logger = logging.getLogger(__name__)

Decider = Callable[[OperationView, NormalizedPspEvent], Decision]
WEBHOOK_ACTOR: ActorType = "WEBHOOK"
MAX_EVENT_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class EventClaim:
    event_id: uuid.UUID
    lease_token: uuid.UUID


@dataclass(frozen=True, slots=True)
class _Prepared:
    """Outcome of the first (locked) step: either finished, or an attempt to finalize."""

    finished_status: str | None
    attempt_id: uuid.UUID | None
    operation_id: uuid.UUID
    review_reason: str | None = None


def claim_psp_events(
    factory: sessionmaker[Session],
    *,
    batch_size: int = 50,
    lease_seconds: int = 30,
    organisation_id: uuid.UUID | None = None,
) -> list[EventClaim]:
    """Lease PENDING events, and PROCESSING ones whose lease lapsed, oldest first.

    An event already claimed ``MAX_EVENT_ATTEMPTS`` times is dead-lettered instead.
    """
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        statement = (
            select(PspEvent)
            .where(
                or_(
                    PspEvent.status == "PENDING",
                    (PspEvent.status == "PROCESSING") & (PspEvent.lease_expires_at <= now),
                ),
                or_(PspEvent.next_attempt_at.is_(None), PspEvent.next_attempt_at <= now),
            )
            .order_by(PspEvent.created_at, PspEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        if organisation_id is not None:
            statement = statement.where(PspEvent.organisation_id == organisation_id)
        claims: list[EventClaim] = []
        for event in session.scalars(statement):
            if event.attempt_count >= MAX_EVENT_ATTEMPTS:
                _finish(event, "DEAD_LETTER", "ATTEMPTS_EXHAUSTED")
                continue
            token = uuid.uuid4()
            event.attempt_count += 1
            event.status = "PROCESSING"
            event.lease_token = token
            event.lease_expires_at = now + timedelta(seconds=lease_seconds)
            claims.append(EventClaim(event.id, token))
    return claims


def _finish(event: PspEvent, status: str, outcome_code: str | None = None) -> None:
    event.status = status
    event.outcome_code = outcome_code
    event.lease_token = None
    event.lease_expires_at = None
    event.processed_at = datetime.now(UTC)


def _identity(result: ProviderResult) -> tuple[str, str, str, str, int, str]:
    return (
        result.accountId,
        result.stableKey,
        result.operationKind,
        result.reference,
        result.amount,
        result.currency,
    )


def _expected_identity(expected: ExpectedProviderResult) -> tuple[str, str, str, str, int, str]:
    return (
        expected.account_id,
        expected.stable_key,
        expected.operation_kind,
        expected.reference,
        expected.amount,
        expected.currency,
    )


def _prepare(
    factory: sessionmaker[Session],
    claim: EventClaim,
    *,
    provider_account_id: str,
    decider: Decider,
) -> _Prepared | None:
    with factory() as session, session.begin():
        event = session.scalar(
            select(PspEvent)
            .where(
                PspEvent.id == claim.event_id,
                PspEvent.lease_token == claim.lease_token,
                PspEvent.status == "PROCESSING",
            )
            .with_for_update()
        )
        if event is None or event.provider_operation_id is None:
            return None
        operation = session.scalar(
            select(ProviderOperation)
            .where(ProviderOperation.id == event.provider_operation_id)
            .with_for_update()
        )
        if operation is None:
            raise RuntimeError("matched provider operation disappeared")
        digest = event.payload_sha256
        recorded = session.scalar(
            select(ProviderAttempt.id).where(
                ProviderAttempt.provider_operation_id == operation.id,
                ProviderAttempt.attempt_kind == "WEBHOOK",
                ProviderAttempt.response_sha256 == digest,
            )
        )
        if recorded is not None:  # crashed after recording: finish the job
            return _Prepared(None, recorded, operation.id)

        try:
            effect = SimulatorEnvelope.model_validate_json(event.payload_bytes).effect
            result = ProviderResult.model_validate_json(canonical_json_bytes(effect))
        except ValidationError:
            _finish(event, "REVIEW", "INVALID_EVIDENCE")
            return _Prepared("REVIEW", None, operation.id, review_reason="INVALID_EVIDENCE")
        expected = expected_result_for(session, operation, provider_account_id=provider_account_id)
        if _identity(result) != _expected_identity(expected):
            _finish(event, "REVIEW", "MISMATCHED_EVIDENCE")
            return _Prepared("REVIEW", None, operation.id, review_reason="MISMATCHED_EVIDENCE")

        applied = frozenset(
            session.scalars(
                select(PspEvent.provider_event_id).where(
                    PspEvent.provider_operation_id == operation.id,
                    PspEvent.status == "APPLIED",
                )
            )
        )
        view = OperationView(
            status=cast(OperationStatus, operation.status),
            kind=operation.kind,
            amount=result.amount,
            applied_event_ids=applied,
        )
        normalized = NormalizedPspEvent(
            provider=event.provider,
            event_id=event.provider_event_id,
            outcome=result.outcome,
            decline_code=result.declineCode,
        )
        try:
            decision = decider(view, normalized)
        except NotImplementedError:
            _finish(event, "DEFERRED", "ORDERING_DECISION_UNAVAILABLE")
            return _Prepared("DEFERRED", None, operation.id)
        if isinstance(decision, Ignore):
            _finish(event, "IGNORED", decision.reason)
            return _Prepared("IGNORED", None, operation.id)
        if not isinstance(decision, Apply):
            _finish(event, "REVIEW", decision.reason)
            return _Prepared("REVIEW", None, operation.id, review_reason=decision.reason)

        sequence = (
            int(
                session.scalar(
                    select(func.coalesce(func.max(ProviderAttempt.sequence), 0)).where(
                        ProviderAttempt.provider_operation_id == operation.id
                    )
                )
                or 0
            )
            + 1
        )
        now = datetime.now(UTC)
        succeeded = decision.outcome == "SUCCEEDED"
        attempt = ProviderAttempt(
            organisation_id=operation.organisation_id,
            environment_id=operation.environment_id,
            provider_operation_id=operation.id,
            sequence=sequence,
            attempt_kind="WEBHOOK",
            state="RESPONSE_RECEIVED",
            request_sha256=hashlib.sha256(event.provider_event_id.encode()).digest(),
            started_at=now,
            completed_at=now,
            response_http_status=200,
            response_bytes=event.payload_bytes,
            response_sha256=digest,
            provider_signature_valid=True,
            classification="VERIFIED_SUCCESS" if succeeded else "VERIFIED_BUSINESS_DECLINE",
            safe_error_code=None if succeeded else (result.declineCode or "PROVIDER_DECLINED"),
        )
        session.add(attempt)
        session.flush([attempt])
        return _Prepared(None, attempt.id, operation.id)


def process_psp_event(
    factory: sessionmaker[Session],
    claim: EventClaim,
    *,
    provider_account_id: str,
    decider: Decider = decide,
) -> str | None:
    """Apply one leased event; returns its final status (``None`` if the lease was lost)."""
    prepared = _prepare(factory, claim, provider_account_id=provider_account_id, decider=decider)
    if prepared is None:
        return None
    correlation_id = f"psp:{claim.event_id}"
    if prepared.review_reason is not None:
        mark_operation_for_review(
            factory,
            operation_id=prepared.operation_id,
            reason=prepared.review_reason,
            evidence_attempt_id=None,
            actor_type=WEBHOOK_ACTOR,
            correlation_id=correlation_id,
        )
        return prepared.finished_status
    if prepared.finished_status is not None:
        return prepared.finished_status
    if prepared.attempt_id is None:
        raise RuntimeError("an applied event must carry its recorded attempt")
    correlation_id = f"psp:{claim.event_id}"
    try:
        finalize_verified_attempt(
            factory,
            operation_id=prepared.operation_id,
            evidence_attempt_id=prepared.attempt_id,
            actor_type=WEBHOOK_ACTOR,
            correlation_id=correlation_id,
        )
    except (RelayPayError, SQLAlchemyError):
        # The lease lapses and the event is retried; the operation reaches review after
        # repeated failures, exactly as a failing lookup does.
        record_apply_failure(
            factory,
            operation_id=prepared.operation_id,
            evidence_attempt_id=prepared.attempt_id,
            correlation_id=correlation_id,
        )
        return None
    with factory() as session, session.begin():
        event = session.scalar(
            select(PspEvent).where(PspEvent.id == claim.event_id).with_for_update()
        )
        if event is not None and event.lease_token == claim.lease_token:
            _finish(event, "APPLIED")
    return "APPLIED"


def run_psp_event_batch(
    factory: sessionmaker[Session],
    *,
    provider_account_id: str,
    batch_size: int = 50,
    organisation_id: uuid.UUID | None = None,
    decider: Decider = decide,
) -> int:
    """Poller task: apply every claimable inbox event once."""
    claims = claim_psp_events(factory, batch_size=batch_size, organisation_id=organisation_id)
    for claim in claims:
        try:
            process_psp_event(
                factory, claim, provider_account_id=provider_account_id, decider=decider
            )
        except Exception:  # one bad event must not stall the rest
            logger.exception("psp_event_apply_failed", extra={"psp_event_id": str(claim.event_id)})
    return len(claims)
