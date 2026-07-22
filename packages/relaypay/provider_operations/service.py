import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx2
from sqlalchemy import func, select, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.payments.models import Authorization, Capture, Refund
from relaypay.provider_operations.finalizer import (
    ActorType,
    finalize_verified_attempt,
    mark_operation_for_review,
    record_apply_failure,
)
from relaypay.provider_operations.models import ProviderAttempt, ProviderOperation
from relaypay.provider_operations.service_types import ProviderObservation
from relaypay.provider_operations.validation import (
    ClassifiedProviderResult,
    ExpectedProviderResult,
    classify_provider_observation,
    transport_error_result,
)


class ProviderTransport(Protocol):
    def mutate(self, request_bytes: bytes) -> ProviderObservation: ...

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation: ...


class HTTPProviderTransport:
    def __init__(self, *, base_url: str, timeout_seconds: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        response = httpx2.post(
            f"{self._base_url}/v1/effects",
            content=request_bytes,
            headers={"Content-Type": "application/json"},
            timeout=self._timeout,
        )
        return ProviderObservation(response.status_code, response.content, dict(response.headers))

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        response = httpx2.get(
            f"{self._base_url}/v1/effects/{stable_key}",
            params={"account_id": account_id},
            timeout=self._timeout,
        )
        return ProviderObservation(response.status_code, response.content, dict(response.headers))


@dataclass(frozen=True, slots=True)
class PreparedOperation:
    operation_id: uuid.UUID
    stable_key: str
    request_bytes: bytes
    already_sent: bool
    terminal: bool


@dataclass(frozen=True, slots=True)
class RecordedOutcome:
    attempt_id: uuid.UUID
    action: str
    review_reason: str | None


def _resource_facts(session: Session, operation: ProviderOperation) -> tuple[str, int, str]:
    if operation.resource_type == "AUTHORIZATION":
        authorization = session.get(Authorization, operation.resource_id)
        if authorization is None:
            raise RuntimeError("provider operation resource is missing")
        return authorization.public_id, authorization.amount, authorization.currency
    elif operation.resource_type == "CAPTURE":
        capture = session.get(Capture, operation.resource_id)
        if capture is None:
            raise RuntimeError("provider operation resource is missing")
        return capture.public_id, capture.amount, capture.currency
    refund = session.get(Refund, operation.resource_id)
    if refund is None:
        raise RuntimeError("provider operation resource is missing")
    return refund.public_id, refund.amount, refund.currency


def _expected_result(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    provider_account_id: str,
) -> ExpectedProviderResult:
    with factory() as session, session.begin():
        operation = session.get(ProviderOperation, operation_id)
        if operation is None:
            raise RuntimeError("provider operation is missing")
        reference, amount, currency = _resource_facts(session, operation)
        return ExpectedProviderResult(
            account_id=provider_account_id,
            stable_key=operation.stable_provider_key,
            operation_kind=operation.kind,
            reference=reference,
            amount=amount,
            currency=currency,
        )


def prepare_first_send(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    operation_public_id: str,
    provider_account_id: str,
) -> PreparedOperation:
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation)
            .where(
                ProviderOperation.organisation_id == organisation_id,
                ProviderOperation.environment_id == environment_id
                if environment_id is not None
                else true(),
                ProviderOperation.public_id == operation_public_id,
            )
            .with_for_update()
        )
        if operation is None:
            raise RuntimeError("provider operation is missing after command initiation")
        if operation.status in {"SUCCEEDED", "FAILED"}:
            return PreparedOperation(operation.id, operation.stable_provider_key, b"", True, True)
        if operation.last_sent_at is not None:
            if operation.provider_request_bytes is None:
                raise RuntimeError("sent operation is missing canonical request bytes")
            return PreparedOperation(
                operation.id,
                operation.stable_provider_key,
                operation.provider_request_bytes,
                True,
                False,
            )

        reference, amount, currency = _resource_facts(session, operation)
        request_bytes = canonical_json_bytes(
            {
                "accountId": provider_account_id,
                "amount": amount,
                "currency": currency,
                "operationKind": operation.kind,
                "reference": reference,
                "stableKey": operation.stable_provider_key,
            }
        )
        request_sha = hashlib.sha256(request_bytes).digest()
        now = datetime.now(UTC)
        operation.provider_request_bytes = request_bytes
        operation.provider_request_sha256 = request_sha
        operation.attempt_count = 1
        operation.last_sent_at = now
        operation.next_lookup_at = now
        session.add(
            ProviderAttempt(
                organisation_id=organisation_id,
                environment_id=operation.environment_id,
                provider_operation_id=operation.id,
                sequence=1,
                attempt_kind="MUTATION",
                state="SENT",
                request_sha256=request_sha,
                started_at=now,
            )
        )
    return PreparedOperation(
        operation.id, operation.stable_provider_key, request_bytes, False, False
    )


def _next_lookup(now: datetime, attempt_count: int) -> datetime:
    return now + timedelta(seconds=min(2 ** max(attempt_count - 2, 0), 60))


def _record_classification(
    attempt: ProviderAttempt,
    *,
    observation: ProviderObservation | None,
    classified: ClassifiedProviderResult,
    now: datetime,
) -> None:
    attempt.completed_at = now
    attempt.state = (
        "TRANSPORT_ERROR"
        if observation is None
        else "VALIDATION_REJECTED"
        if classified.action == "REVIEW"
        else "RESPONSE_RECEIVED"
    )
    attempt.response_http_status = observation.status_code if observation else None
    attempt.response_bytes = observation.body if observation else None
    attempt.response_sha256 = hashlib.sha256(observation.body).digest() if observation else None
    attempt.provider_signature_valid = classified.signature_valid
    attempt.classification = classified.classification
    attempt.safe_error_code = classified.decline_code or classified.safe_error_code


def _complete_mutation(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    observation: ProviderObservation | None,
    classified: ClassifiedProviderResult,
) -> RecordedOutcome | None:
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.id == operation_id).with_for_update()
        )
        if operation is None:
            raise RuntimeError("provider operation disappeared after send")
        attempt = session.scalar(
            select(ProviderAttempt)
            .where(
                ProviderAttempt.provider_operation_id == operation_id,
                ProviderAttempt.attempt_kind == "MUTATION",
            )
            .with_for_update()
        )
        if attempt is None or attempt.completed_at is not None:
            return None
        now = datetime.now(UTC)
        _record_classification(attempt, observation=observation, classified=classified, now=now)
        operation.next_lookup_at = (
            now if classified.action == "LOOKUP" else _next_lookup(now, operation.attempt_count)
        )
        return RecordedOutcome(
            attempt.id,
            classified.action,
            "INVALID_EVIDENCE" if classified.action == "REVIEW" else None,
        )


def record_lookup_observation(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    lease_token: uuid.UUID | None,
    observation: ProviderObservation | None,
    classified: ClassifiedProviderResult,
    indeterminate_after: int = 5,
) -> RecordedOutcome | None:
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.id == operation_id).with_for_update()
        )
        if operation is None or operation.status in {"SUCCEEDED", "FAILED"}:
            return None
        if lease_token is not None and operation.lookup_lease_token != lease_token:
            return None
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
        request_sha = hashlib.sha256(
            canonical_json_bytes(
                {"accountId": "configured", "stableKey": operation.stable_provider_key}
            )
        ).digest()
        attempt = ProviderAttempt(
            organisation_id=operation.organisation_id,
            environment_id=operation.environment_id,
            provider_operation_id=operation.id,
            sequence=sequence,
            attempt_kind="LOOKUP",
            state="RESPONSE_RECEIVED",
            request_sha256=request_sha,
            started_at=now,
        )
        _record_classification(attempt, observation=observation, classified=classified, now=now)
        session.add(attempt)
        operation.attempt_count += 1
        operation.lookup_lease_token = None
        operation.lookup_lease_expires_at = None
        operation.next_lookup_at = _next_lookup(now, operation.attempt_count)

        lookup_count = sequence - 1
        review_reason = "INVALID_EVIDENCE" if classified.action == "REVIEW" else None
        action = classified.action
        if action in {"LOOKUP", "PROCESSING"} and lookup_count >= indeterminate_after:
            action = "REVIEW"
            review_reason = "PROVIDER_INDETERMINATE"
        session.flush([attempt])
        return RecordedOutcome(attempt.id, action, review_reason)


def apply_recorded_outcome(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    outcome: RecordedOutcome | None,
    actor_type: ActorType,
    correlation_id: str,
) -> None:
    if outcome is None:
        return
    if outcome.action in {"FINALIZE_SUCCESS", "FINALIZE_FAILURE"}:
        try:
            finalize_verified_attempt(
                factory,
                operation_id=operation_id,
                evidence_attempt_id=outcome.attempt_id,
                actor_type=actor_type,
                correlation_id=correlation_id,
            )
        except (RelayPayError, SQLAlchemyError):
            record_apply_failure(
                factory,
                operation_id=operation_id,
                evidence_attempt_id=outcome.attempt_id,
                correlation_id=correlation_id,
            )
    elif outcome.action == "REVIEW":
        mark_operation_for_review(
            factory,
            operation_id=operation_id,
            reason=outcome.review_reason or "INVALID_EVIDENCE",
            evidence_attempt_id=outcome.attempt_id,
            actor_type=actor_type,
            correlation_id=correlation_id,
        )


def classify_and_record_lookup(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    lease_token: uuid.UUID | None,
    provider_account_id: str,
    provider_signing_secret: str,
    observation: ProviderObservation | None,
) -> RecordedOutcome | None:
    expected = _expected_result(
        factory, operation_id=operation_id, provider_account_id=provider_account_id
    )
    classified = (
        transport_error_result()
        if observation is None
        else classify_provider_observation(
            observation,
            expected=expected,
            signing_secret=provider_signing_secret,
            is_lookup=True,
        )
    )
    return record_lookup_observation(
        factory,
        operation_id=operation_id,
        lease_token=lease_token,
        observation=observation,
        classified=classified,
    )


def dispatch_operation(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    operation_public_id: str,
    provider_account_id: str,
    transport: ProviderTransport,
    provider_signing_secret: str,
) -> None:
    prepared = prepare_first_send(
        factory,
        organisation_id=organisation_id,
        environment_id=environment_id,
        operation_public_id=operation_public_id,
        provider_account_id=provider_account_id,
    )
    if prepared.terminal:
        return
    expected = _expected_result(
        factory,
        operation_id=prepared.operation_id,
        provider_account_id=provider_account_id,
    )
    if prepared.already_sent:
        try:
            observation = transport.lookup(
                account_id=provider_account_id, stable_key=prepared.stable_key
            )
        except Exception:
            observation = None
        classified = (
            transport_error_result()
            if observation is None
            else classify_provider_observation(
                observation,
                expected=expected,
                signing_secret=provider_signing_secret,
                is_lookup=True,
            )
        )
        outcome = record_lookup_observation(
            factory,
            operation_id=prepared.operation_id,
            lease_token=None,
            observation=observation,
            classified=classified,
        )
    else:
        try:
            observation = transport.mutate(prepared.request_bytes)
        except Exception:
            observation = None
        classified = (
            transport_error_result()
            if observation is None
            else classify_provider_observation(
                observation,
                expected=expected,
                signing_secret=provider_signing_secret,
                is_lookup=False,
            )
        )
        outcome = _complete_mutation(
            factory,
            operation_id=prepared.operation_id,
            observation=observation,
            classified=classified,
        )
    apply_recorded_outcome(
        factory,
        operation_id=prepared.operation_id,
        outcome=outcome,
        actor_type="REQUEST",
        correlation_id=f"dispatch:{prepared.operation_id}",
    )
