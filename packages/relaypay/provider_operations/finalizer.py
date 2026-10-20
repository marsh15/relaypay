import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.event_delivery.service import append_merchant_event
from relaypay.idempotency import canonical_json_bytes
from relaypay.ledger.service import post_capture_journal, post_refund_journal
from relaypay.payments.models import Authorization, Capture, PaymentIntent, Refund, Void
from relaypay.payments.transitions import PaymentStatus, transition
from relaypay.proof.crashpoints import maybe_crash
from relaypay.provider_operations.models import (
    IdempotencyRecord,
    OperationHistory,
    ProviderAttempt,
    ProviderOperation,
)

ActorType = Literal["REQUEST", "RECOVERY_WORKER", "ADMIN_LOOKUP", "FINALIZER"]


@dataclass(frozen=True, slots=True)
class FinalizedResult:
    status_code: int
    body: bytes
    headers: dict[str, str]
    already_finalized: bool


def _resource(
    session: Session, operation: ProviderOperation, *, lock: bool
) -> Authorization | Capture | Refund | Void:
    resource: Authorization | Capture | Refund | Void | None
    if operation.resource_type == "AUTHORIZATION":
        statement = select(Authorization).where(Authorization.id == operation.resource_id)
        resource = session.scalar(statement.with_for_update() if lock else statement)
    elif operation.resource_type == "CAPTURE":
        capture_statement = select(Capture).where(Capture.id == operation.resource_id)
        resource = session.scalar(
            capture_statement.with_for_update() if lock else capture_statement
        )
    elif operation.resource_type == "VOID":
        void_statement = select(Void).where(Void.id == operation.resource_id)
        resource = session.scalar(void_statement.with_for_update() if lock else void_statement)
    else:
        refund_statement = select(Refund).where(Refund.id == operation.resource_id)
        resource = session.scalar(refund_statement.with_for_update() if lock else refund_statement)
    if resource is None:
        raise RuntimeError("provider operation resource binding is missing")
    if resource.organisation_id != operation.organisation_id:
        raise RuntimeError("provider operation resource crosses an organisation boundary")
    return resource


def _locked_graph(
    session: Session, operation_id: uuid.UUID
) -> tuple[
    PaymentIntent,
    ProviderOperation,
    Authorization | Capture | Refund | Void,
    list[IdempotencyRecord],
]:
    initial = session.get(ProviderOperation, operation_id)
    if initial is None:
        raise RuntimeError("provider operation is missing")
    payment = session.scalar(
        select(PaymentIntent).where(PaymentIntent.id == initial.payment_intent_id).with_for_update()
    )
    if payment is None:
        raise RuntimeError("provider operation payment is missing")
    operation = session.scalar(
        select(ProviderOperation).where(ProviderOperation.id == operation_id).with_for_update()
    )
    if operation is None:
        raise RuntimeError("provider operation is missing")
    resource = _resource(session, operation, lock=True)
    records = list(
        session.scalars(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.provider_operation_id == operation.id)
            .order_by(IdempotencyRecord.id)
            .with_for_update()
        )
    )
    return payment, operation, resource, records


def _refund_target_status(session: Session, payment: PaymentIntent) -> PaymentStatus:
    """``refunded`` once SUCCEEDED refunds sum to the captured amount, else ``partially_refunded``.

    Called after the refund row was set SUCCEEDED in this session (the query autoflushes it).
    """
    captured = session.scalar(
        select(Capture.amount).where(
            Capture.payment_intent_id == payment.id, Capture.status == "SUCCEEDED"
        )
    )
    refunded = session.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.payment_intent_id == payment.id, Refund.status == "SUCCEEDED"
        )
    )
    if captured is not None and int(refunded or 0) == captured:
        return PaymentStatus.REFUNDED
    return PaymentStatus.PARTIALLY_REFUNDED


def _terminal_result(operation: ProviderOperation, *, already: bool) -> FinalizedResult:
    if operation.terminal_http_status is None or operation.terminal_response_bytes is None:
        raise RuntimeError("terminal operation is missing canonical response evidence")
    return FinalizedResult(
        operation.terminal_http_status,
        operation.terminal_response_bytes,
        dict(operation.terminal_response_headers or {}),
        already,
    )


def finalize_verified_attempt(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    evidence_attempt_id: uuid.UUID,
    actor_type: ActorType,
    correlation_id: str,
) -> FinalizedResult:
    with factory() as session, session.begin():
        payment, operation, resource, records = _locked_graph(session, operation_id)
        if operation.status in {"SUCCEEDED", "FAILED"}:
            return _terminal_result(operation, already=True)

        attempt = session.get(ProviderAttempt, evidence_attempt_id)
        if attempt is None or attempt.provider_operation_id != operation.id:
            raise RuntimeError("finalization evidence does not belong to the operation")
        if attempt.provider_signature_valid is not True:
            raise RuntimeError("unverified provider evidence cannot finalize an operation")
        if attempt.classification not in {
            "VERIFIED_SUCCESS",
            "VERIFIED_BUSINESS_DECLINE",
            "VERIFIED_NOT_FOUND_FINAL",  # ADR-011: signed absence past the grace period
        }:
            raise RuntimeError("provider evidence is not a verified terminal outcome")

        now = datetime.now(UTC)
        prior_status = operation.status
        succeeded = attempt.classification == "VERIFIED_SUCCESS"
        terminal_status = "SUCCEEDED" if succeeded else "FAILED"
        failure_code = None if succeeded else attempt.safe_error_code
        if not succeeded and not failure_code:
            raise RuntimeError("verified business decline is missing its decline code")

        if isinstance(resource, Authorization):
            resource.status = terminal_status
            resource.authorized_at = now if succeeded else None  # DB derives expires_at
            resource.failure_code = failure_code
            event_type = "payment.authorized.v1"
            transition(
                session,
                payment,
                PaymentStatus.AUTHORISED if succeeded else PaymentStatus.FAILED,
                reason="AUTHORIZATION_SUCCEEDED" if succeeded else "AUTHORIZATION_DECLINED",
                actor=actor_type,
            )
        elif isinstance(resource, Capture):
            event_type = "payment.captured.v1"
            if succeeded:
                journal = post_capture_journal(
                    session,
                    organisation_id=operation.organisation_id,
                    environment_id=operation.environment_id,
                    provider_operation_id=operation.id,
                    capture_id=resource.id,
                    amount=resource.amount,
                )
                resource.journal_id = journal.journal_id
            resource.status = terminal_status
            resource.captured_at = now if succeeded else None
            resource.failure_code = failure_code
            # A declined capture is terminal for the singleton capture, so the payment fails.
            transition(
                session,
                payment,
                PaymentStatus.CAPTURED if succeeded else PaymentStatus.FAILED,
                reason="CAPTURE_SUCCEEDED" if succeeded else "CAPTURE_DECLINED",
                actor=actor_type,
            )
        elif isinstance(resource, Void):
            # A void releases the hold: nothing is posted, and no merchant event type exists for
            # it yet (merchant events gain non-capture parents in M17).
            event_type = None
            resource.status = terminal_status
            resource.voided_at = now if succeeded else None
            resource.failure_code = failure_code
            if succeeded:
                # A declined void leaves the payment authorised and capturable.
                transition(
                    session,
                    payment,
                    PaymentStatus.CANCELLED,
                    reason="VOID_SUCCEEDED",
                    actor=actor_type,
                )
        else:
            event_type = "refund.succeeded.v1"
            if succeeded:
                journal = post_refund_journal(
                    session,
                    organisation_id=operation.organisation_id,
                    environment_id=operation.environment_id,
                    provider_operation_id=operation.id,
                    refund_id=resource.id,
                    amount=resource.amount,
                )
                resource.journal_id = journal.journal_id
            resource.status = terminal_status
            resource.refunded_at = now if succeeded else None
            resource.failure_code = failure_code
            if succeeded:
                # A declined refund leaves the payment status unchanged.
                transition(
                    session,
                    payment,
                    _refund_target_status(session, payment),
                    reason="REFUND_SUCCEEDED",
                    actor=actor_type,
                )

        if succeeded and event_type is not None:
            append_merchant_event(
                session,
                payment=payment,
                operation=operation,
                resource_public_id=resource.public_id,
                amount=resource.amount,
                currency=resource.currency,
                event_type=event_type,
                occurred_at=now,
            )

        response = canonical_json_bytes(
            {
                "amount": resource.amount,
                "currency": resource.currency,
                "failureCode": failure_code,
                "operationId": operation.public_id,
                "paymentIntentId": payment.public_id,
                "resourceId": resource.public_id,
                "status": terminal_status,
            }
        )
        response_sha = hashlib.sha256(response).digest()
        response_headers = {"Content-Type": "application/json"}
        operation.status = terminal_status
        operation.review_reason = None
        operation.terminal_http_status = 200
        operation.terminal_response_headers = response_headers
        operation.terminal_response_bytes = response
        operation.terminal_response_sha256 = response_sha
        operation.finalized_at = now
        operation.lookup_lease_token = None
        operation.lookup_lease_expires_at = None
        operation.next_lookup_at = None

        for record in records:
            record.is_terminal = True
            record.http_status = 200
            record.response_headers = response_headers
            record.response_bytes = response
            record.response_sha256 = response_sha
            record.finalized_at = now

        session.add(
            OperationHistory(
                organisation_id=operation.organisation_id,
                environment_id=operation.environment_id,
                provider_operation_id=operation.id,
                from_status=prior_status,
                to_status=terminal_status,
                reason_code=attempt.classification,
                evidence_attempt_id=attempt.id,
                actor_type=actor_type,
                correlation_id=correlation_id,
            )
        )
    maybe_crash("finalizer.after_commit")
    return _terminal_result(operation, already=False)


def mark_operation_for_review(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    reason: str,
    evidence_attempt_id: uuid.UUID | None,
    actor_type: ActorType,
    correlation_id: str,
) -> None:
    with factory() as session, session.begin():
        _, operation, resource, _ = _locked_graph(session, operation_id)
        if operation.status in {"SUCCEEDED", "FAILED"}:
            return
        prior_status = operation.status
        operation.status = "REQUIRES_REVIEW"
        operation.review_reason = reason
        operation.next_lookup_at = None
        operation.lookup_lease_token = None
        operation.lookup_lease_expires_at = None
        resource.status = "REQUIRES_REVIEW"
        session.add(
            OperationHistory(
                organisation_id=operation.organisation_id,
                environment_id=operation.environment_id,
                provider_operation_id=operation.id,
                from_status=prior_status,
                to_status="REQUIRES_REVIEW",
                reason_code=reason,
                evidence_attempt_id=evidence_attempt_id,
                actor_type=actor_type,
                correlation_id=correlation_id,
            )
        )


def record_apply_failure(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    evidence_attempt_id: uuid.UUID,
    correlation_id: str,
    review_after: int = 3,
) -> int:
    with factory() as session, session.begin():
        _, operation, resource, _ = _locked_graph(session, operation_id)
        if operation.status in {"SUCCEEDED", "FAILED"}:
            return operation.apply_failure_count
        prior_status = operation.status
        operation.apply_failure_count += 1
        count = operation.apply_failure_count
        if count >= review_after:
            operation.status = "REQUIRES_REVIEW"
            operation.review_reason = "APPLY_FAILURE"
            operation.next_lookup_at = None
            resource.status = "REQUIRES_REVIEW"
            session.add(
                OperationHistory(
                    organisation_id=operation.organisation_id,
                    environment_id=operation.environment_id,
                    provider_operation_id=operation.id,
                    from_status=prior_status,
                    to_status="REQUIRES_REVIEW",
                    reason_code="APPLY_FAILURE",
                    evidence_attempt_id=evidence_attempt_id,
                    actor_type="FINALIZER",
                    correlation_id=correlation_id,
                )
            )
        return count
