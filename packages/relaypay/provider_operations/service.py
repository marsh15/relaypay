import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx2
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.idempotency import canonical_json_bytes
from relaypay.payments.models import Authorization, Capture, Refund
from relaypay.provider_operations.models import ProviderAttempt, ProviderOperation


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    status_code: int
    body: bytes
    headers: dict[str, str]


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


def _resource_facts(session: Session, operation: ProviderOperation) -> tuple[str, int, str]:
    if operation.resource_type == "AUTHORIZATION":
        authorization = session.get(Authorization, operation.resource_id)
        if authorization is None:
            raise RuntimeError("provider operation resource is missing")
        return authorization.public_id, authorization.amount, authorization.currency
    if operation.resource_type == "CAPTURE":
        capture = session.get(Capture, operation.resource_id)
        if capture is None:
            raise RuntimeError("provider operation resource is missing")
        return capture.public_id, capture.amount, capture.currency
    refund = session.get(Refund, operation.resource_id)
    if refund is None:
        raise RuntimeError("provider operation resource is missing")
    return refund.public_id, refund.amount, refund.currency


def prepare_first_send(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    operation_public_id: str,
    provider_account_id: str,
) -> PreparedOperation:
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation)
            .where(
                ProviderOperation.organisation_id == organisation_id,
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


def _complete_mutation(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    observation: ProviderObservation | None,
    transport_error: bool,
) -> None:
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
            return
        attempt.completed_at = datetime.now(UTC)
        if transport_error or observation is None:
            attempt.state = "TRANSPORT_ERROR"
            attempt.classification = "AMBIGUOUS_TRANSPORT"
            attempt.safe_error_code = "PROVIDER_TRANSPORT_ERROR"
        else:
            attempt.state = "RESPONSE_RECEIVED"
            attempt.response_http_status = observation.status_code
            attempt.response_bytes = observation.body
            attempt.response_sha256 = hashlib.sha256(observation.body).digest()
            attempt.classification = (
                "UNCLASSIFIED_RESPONSE"
                if 200 <= observation.status_code < 500
                else "AMBIGUOUS_HTTP"
            )
        operation.next_lookup_at = datetime.now(UTC)


def _record_lookup(
    factory: sessionmaker[Session],
    *,
    operation_id: uuid.UUID,
    observation: ProviderObservation | None,
    transport_error: bool,
) -> None:
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.id == operation_id).with_for_update()
        )
        if operation is None:
            raise RuntimeError("provider operation disappeared before lookup recording")
        sequence = session.scalar(
            select(func.coalesce(func.max(ProviderAttempt.sequence), 0)).where(
                ProviderAttempt.provider_operation_id == operation.id
            )
        )
        request_sha = hashlib.sha256(
            canonical_json_bytes(
                {"accountId": "configured", "stableKey": operation.stable_provider_key}
            )
        ).digest()
        attempt = ProviderAttempt(
            organisation_id=operation.organisation_id,
            provider_operation_id=operation.id,
            sequence=int(sequence or 0) + 1,
            attempt_kind="LOOKUP",
            state="TRANSPORT_ERROR" if transport_error else "RESPONSE_RECEIVED",
            request_sha256=request_sha,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            safe_error_code="PROVIDER_TRANSPORT_ERROR" if transport_error else None,
            classification="AMBIGUOUS_TRANSPORT" if transport_error else "UNCLASSIFIED_RESPONSE",
            response_http_status=observation.status_code if observation else None,
            response_bytes=observation.body if observation else None,
            response_sha256=hashlib.sha256(observation.body).digest() if observation else None,
        )
        session.add(attempt)


def dispatch_operation(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    operation_public_id: str,
    provider_account_id: str,
    transport: ProviderTransport,
) -> None:
    prepared = prepare_first_send(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_public_id,
        provider_account_id=provider_account_id,
    )
    if prepared.terminal:
        return
    if prepared.already_sent:
        try:
            observation = transport.lookup(
                account_id=provider_account_id, stable_key=prepared.stable_key
            )
        except Exception:
            _record_lookup(
                factory,
                operation_id=prepared.operation_id,
                observation=None,
                transport_error=True,
            )
        else:
            _record_lookup(
                factory,
                operation_id=prepared.operation_id,
                observation=observation,
                transport_error=False,
            )
        return
    try:
        observation = transport.mutate(prepared.request_bytes)
    except Exception:
        _complete_mutation(
            factory,
            operation_id=prepared.operation_id,
            observation=None,
            transport_error=True,
        )
    else:
        _complete_mutation(
            factory,
            operation_id=prepared.operation_id,
            observation=observation,
            transport_error=False,
        )
