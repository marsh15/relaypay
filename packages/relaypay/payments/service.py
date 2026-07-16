import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.contracts import CustomerCreate, PaymentIntentCreate, RefundCreate
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import Fingerprint, canonical_json_bytes, digest_secret, key_hint
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import Authorization, Capture, Customer, PaymentIntent, Refund
from relaypay.provider_operations.models import IdempotencyRecord, ProviderOperation


@dataclass(frozen=True, slots=True)
class HTTPResult:
    status_code: int
    body: bytes
    headers: dict[str, str]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class CreatedCustomer:
    public_id: str
    merchant_customer_reference: str
    display_name: str | None


CommandKind = Literal["AUTHORIZE", "CAPTURE", "REFUND"]


def _terminal_result(record: IdempotencyRecord, *, replayed: bool) -> HTTPResult:
    if record.http_status is None or record.response_bytes is None:
        raise RuntimeError("terminal idempotency record is missing its stored response")
    headers = dict(record.response_headers or {})
    if replayed:
        headers["Idempotency-Replayed"] = "true"
    return HTTPResult(record.http_status, record.response_bytes, headers, replayed=replayed)


def _validate_fingerprint(record: IdempotencyRecord, fingerprint: Fingerprint) -> None:
    if record.fingerprint_sha256 != fingerprint.sha256:
        raise RelayPayError(
            code="IDEMPOTENCY_KEY_REUSED",
            message="The idempotency key is already bound to different canonical input",
            http_status=409,
        )


def _existing_key(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    key_digest: bytes,
) -> IdempotencyRecord | None:
    return session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.organisation_id == organisation_id,
            IdempotencyRecord.key_digest == key_digest,
        )
    )


def create_customer(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    payload: CustomerCreate,
) -> CreatedCustomer:
    try:
        with factory() as session, session.begin():
            customer = Customer(
                id=new_uuid(),
                public_id=new_public_id("cus"),
                organisation_id=organisation_id,
                merchant_customer_reference=payload.merchant_customer_reference,
                display_name=payload.display_name,
            )
            session.add(customer)
        return CreatedCustomer(
            customer.public_id, customer.merchant_customer_reference, customer.display_name
        )
    except IntegrityError as error:
        raise RelayPayError(
            code="MERCHANT_CUSTOMER_REFERENCE_CONFLICT",
            message="The merchant customer reference already exists",
            http_status=409,
        ) from error


def create_payment_intent(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    payload: PaymentIntentCreate,
    idempotency_key: str,
    fingerprint: Fingerprint,
    key_pepper: str,
) -> HTTPResult:
    presented_key_digest = digest_secret(idempotency_key, key_pepper)
    payment_id = new_uuid()
    payment_public_id = new_public_id("pay")
    now = datetime.now(UTC)

    try:
        with factory() as session, session.begin():
            existing = _existing_key(
                session,
                organisation_id=organisation_id,
                key_digest=presented_key_digest,
            )
            if existing is not None:
                _validate_fingerprint(existing, fingerprint)
                return _terminal_result(existing, replayed=True)

            customer = session.scalar(
                select(Customer).where(
                    Customer.organisation_id == organisation_id,
                    Customer.public_id == payload.customer_id,
                )
            )
            if customer is None:
                raise not_found("Customer")

            payment = PaymentIntent(
                id=payment_id,
                public_id=payment_public_id,
                organisation_id=organisation_id,
                customer_id=customer.id,
                merchant_reference=payload.merchant_reference,
                amount=payload.amount,
                currency=payload.currency,
            )
            response = canonical_json_bytes(
                {
                    "id": payment_public_id,
                    "customerId": customer.public_id,
                    "merchantReference": payload.merchant_reference,
                    "amount": payload.amount,
                    "currency": payload.currency,
                    "status": "REQUIRES_AUTHORIZATION",
                }
            )
            session.add(payment)
            session.add(
                IdempotencyRecord(
                    organisation_id=organisation_id,
                    key_digest=presented_key_digest,
                    key_hint=key_hint(idempotency_key),
                    fingerprint_sha256=fingerprint.sha256,
                    fingerprint_summary=fingerprint.safe_summary,
                    target_type="PAYMENT_INTENT",
                    target_id=payment_id,
                    provider_operation_id=None,
                    is_terminal=True,
                    http_status=201,
                    response_headers={"Content-Type": "application/json"},
                    response_bytes=response,
                    response_sha256=hashlib.sha256(response).digest(),
                    finalized_at=now,
                )
            )
        return HTTPResult(201, response, {"Content-Type": "application/json"})
    except RelayPayError:
        raise
    except IntegrityError as error:
        with factory() as session, session.begin():
            winner = _existing_key(
                session,
                organisation_id=organisation_id,
                key_digest=presented_key_digest,
            )
            if winner is not None:
                _validate_fingerprint(winner, fingerprint)
                return _terminal_result(winner, replayed=True)
            conflicting_payment = session.scalar(
                select(PaymentIntent).where(
                    PaymentIntent.organisation_id == organisation_id,
                    PaymentIntent.merchant_reference == payload.merchant_reference,
                )
            )
            if conflicting_payment is not None:
                raise RelayPayError(
                    code="MERCHANT_REFERENCE_CONFLICT",
                    message="The merchant payment reference already exists",
                    http_status=409,
                ) from error
        raise RelayPayError(
            code="COMMAND_CONFLICT",
            message="A concurrent command selected another durable winner",
            http_status=409,
        ) from error


def _operation_envelope(operation: ProviderOperation, resource_public_id: str) -> HTTPResult:
    if operation.status in {"SUCCEEDED", "FAILED"}:
        if operation.terminal_http_status is None or operation.terminal_response_bytes is None:
            raise RuntimeError("terminal provider operation is missing its stored response")
        return HTTPResult(
            operation.terminal_http_status,
            operation.terminal_response_bytes,
            dict(operation.terminal_response_headers or {}),
        )
    return HTTPResult(
        202,
        canonical_json_bytes(
            {
                "operationId": operation.public_id,
                "resourceId": resource_public_id,
                "status": operation.status,
                "reviewReason": operation.review_reason,
            }
        ),
        {"Content-Type": "application/json"},
    )


def _attach_key(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    operation: ProviderOperation,
    resource_public_id: str,
    idempotency_key: str,
    key_digest: bytes,
    fingerprint: Fingerprint,
) -> HTTPResult:
    terminal = operation.status in {"SUCCEEDED", "FAILED"}
    session.add(
        IdempotencyRecord(
            organisation_id=organisation_id,
            key_digest=key_digest,
            key_hint=key_hint(idempotency_key),
            fingerprint_sha256=fingerprint.sha256,
            fingerprint_summary=fingerprint.safe_summary,
            target_type="PROVIDER_OPERATION",
            target_id=operation.id,
            provider_operation_id=operation.id,
            is_terminal=terminal,
            http_status=operation.terminal_http_status if terminal else None,
            response_headers=operation.terminal_response_headers if terminal else None,
            response_bytes=operation.terminal_response_bytes if terminal else None,
            response_sha256=operation.terminal_response_sha256 if terminal else None,
            finalized_at=operation.finalized_at if terminal else None,
        )
    )
    return _operation_envelope(operation, resource_public_id)


def _existing_operation_result(
    session: Session,
    *,
    record: IdempotencyRecord,
    fingerprint: Fingerprint,
) -> HTTPResult:
    _validate_fingerprint(record, fingerprint)
    if record.is_terminal:
        return _terminal_result(record, replayed=True)
    operation = session.get(ProviderOperation, record.provider_operation_id)
    if operation is None:
        raise RuntimeError("target-bound idempotency record has no provider operation")
    resource_public_id = _resource_public_id(session, operation)
    return _operation_envelope(operation, resource_public_id)


def _resource_public_id(session: Session, operation: ProviderOperation) -> str:
    if operation.resource_type == "AUTHORIZATION":
        authorization = session.get(Authorization, operation.resource_id)
        if authorization is None:
            raise RuntimeError("provider operation resource binding is missing")
        return authorization.public_id
    if operation.resource_type == "CAPTURE":
        capture = session.get(Capture, operation.resource_id)
        if capture is None:
            raise RuntimeError("provider operation resource binding is missing")
        return capture.public_id
    refund = session.get(Refund, operation.resource_id)
    if refund is None:
        raise RuntimeError("provider operation resource binding is missing")
    return refund.public_id


def _new_operation(
    *,
    organisation_id: uuid.UUID,
    payment: PaymentIntent,
    resource_id: uuid.UUID,
    resource_type: str,
    kind: CommandKind,
    stable_provider_key: str,
) -> ProviderOperation:
    return ProviderOperation(
        id=new_uuid(),
        public_id=new_public_id("op"),
        organisation_id=organisation_id,
        payment_intent_id=payment.id,
        resource_type=resource_type,
        resource_id=resource_id,
        kind=kind,
        stable_provider_key=stable_provider_key,
        status="PROCESSING",
        attempt_count=0,
        apply_failure_count=0,
    )


def initiate_authorization(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    payment_public_id: str,
    idempotency_key: str,
    fingerprint: Fingerprint,
    key_pepper: str,
) -> HTTPResult:
    key_digest = digest_secret(idempotency_key, key_pepper)
    try:
        with factory() as session, session.begin():
            payment = _locked_payment(session, organisation_id, payment_public_id)
            existing_key = _existing_key(
                session, organisation_id=organisation_id, key_digest=key_digest
            )
            if existing_key is not None:
                return _existing_operation_result(
                    session, record=existing_key, fingerprint=fingerprint
                )

            existing_resource = session.scalar(
                select(Authorization).where(
                    Authorization.organisation_id == organisation_id,
                    Authorization.payment_intent_id == payment.id,
                )
            )
            if existing_resource is not None:
                operation = _lock_operation_then_resource(
                    session, existing_resource.provider_operation_id, Authorization
                )
                return _attach_key(
                    session,
                    organisation_id=organisation_id,
                    operation=operation,
                    resource_public_id=existing_resource.public_id,
                    idempotency_key=idempotency_key,
                    key_digest=key_digest,
                    fingerprint=fingerprint,
                )

            resource_id = new_uuid()
            operation = _new_operation(
                organisation_id=organisation_id,
                payment=payment,
                resource_id=resource_id,
                resource_type="AUTHORIZATION",
                kind="AUTHORIZE",
                stable_provider_key=f"authorize:{payment.public_id}",
            )
            authorization = Authorization(
                id=resource_id,
                public_id=new_public_id("auth"),
                organisation_id=organisation_id,
                payment_intent_id=payment.id,
                provider_operation_id=operation.id,
                amount=payment.amount,
                currency="INR",
                status="PROCESSING",
            )
            session.add_all([operation, authorization])
            session.flush([operation, authorization])
            return _attach_key(
                session,
                organisation_id=organisation_id,
                operation=operation,
                resource_public_id=authorization.public_id,
                idempotency_key=idempotency_key,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )
    except RelayPayError:
        raise
    except IntegrityError as error:
        return _recover_operation_race(
            factory,
            organisation_id=organisation_id,
            key_digest=key_digest,
            fingerprint=fingerprint,
            error=error,
        )


def initiate_capture(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    payment_public_id: str,
    idempotency_key: str,
    fingerprint: Fingerprint,
    key_pepper: str,
) -> HTTPResult:
    key_digest = digest_secret(idempotency_key, key_pepper)
    try:
        with factory() as session, session.begin():
            payment = _locked_payment(session, organisation_id, payment_public_id)
            existing_key = _existing_key(
                session, organisation_id=organisation_id, key_digest=key_digest
            )
            if existing_key is not None:
                return _existing_operation_result(
                    session, record=existing_key, fingerprint=fingerprint
                )
            authorization = session.scalar(
                select(Authorization).where(
                    Authorization.organisation_id == organisation_id,
                    Authorization.payment_intent_id == payment.id,
                )
            )
            if authorization is None or authorization.status != "SUCCEEDED":
                raise RelayPayError(
                    code="CAPTURE_REQUIRES_SUCCEEDED_AUTHORIZATION",
                    message="Capture requires a verified succeeded authorization",
                    http_status=409,
                )
            existing_resource = session.scalar(
                select(Capture).where(
                    Capture.organisation_id == organisation_id,
                    Capture.payment_intent_id == payment.id,
                )
            )
            if existing_resource is not None:
                operation = _lock_operation_then_resource(
                    session, existing_resource.provider_operation_id, Capture
                )
                return _attach_key(
                    session,
                    organisation_id=organisation_id,
                    operation=operation,
                    resource_public_id=existing_resource.public_id,
                    idempotency_key=idempotency_key,
                    key_digest=key_digest,
                    fingerprint=fingerprint,
                )
            resource_id = new_uuid()
            operation = _new_operation(
                organisation_id=organisation_id,
                payment=payment,
                resource_id=resource_id,
                resource_type="CAPTURE",
                kind="CAPTURE",
                stable_provider_key=f"capture:{payment.public_id}",
            )
            capture = Capture(
                id=resource_id,
                public_id=new_public_id("cap"),
                organisation_id=organisation_id,
                payment_intent_id=payment.id,
                authorization_id=authorization.id,
                provider_operation_id=operation.id,
                amount=payment.amount,
                currency="INR",
                status="PROCESSING",
            )
            session.add_all([operation, capture])
            session.flush([operation, capture])
            return _attach_key(
                session,
                organisation_id=organisation_id,
                operation=operation,
                resource_public_id=capture.public_id,
                idempotency_key=idempotency_key,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )
    except RelayPayError:
        raise
    except IntegrityError as error:
        return _recover_operation_race(
            factory,
            organisation_id=organisation_id,
            key_digest=key_digest,
            fingerprint=fingerprint,
            error=error,
        )


def initiate_refund(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    payment_public_id: str,
    payload: RefundCreate,
    idempotency_key: str,
    fingerprint: Fingerprint,
    key_pepper: str,
) -> HTTPResult:
    key_digest = digest_secret(idempotency_key, key_pepper)
    try:
        with factory() as session, session.begin():
            payment = _locked_payment(session, organisation_id, payment_public_id)
            existing_key = _existing_key(
                session, organisation_id=organisation_id, key_digest=key_digest
            )
            if existing_key is not None:
                return _existing_operation_result(
                    session, record=existing_key, fingerprint=fingerprint
                )
            capture = session.scalar(
                select(Capture).where(
                    Capture.organisation_id == organisation_id,
                    Capture.payment_intent_id == payment.id,
                    Capture.status == "SUCCEEDED",
                )
            )
            if capture is None:
                raise RelayPayError(
                    code="REFUND_REQUIRES_SUCCEEDED_CAPTURE",
                    message="Refund requires a verified succeeded capture",
                    http_status=409,
                )
            reserved = session.scalar(
                select(func.coalesce(func.sum(Refund.amount), 0)).where(
                    Refund.organisation_id == organisation_id,
                    Refund.payment_intent_id == payment.id,
                    Refund.status.in_(("PROCESSING", "REQUIRES_REVIEW", "SUCCEEDED")),
                )
            )
            available = capture.amount - int(reserved or 0)
            if payload.amount > available:
                raise RelayPayError(
                    code="REFUND_AMOUNT_EXCEEDS_AVAILABLE",
                    message="Refund amount exceeds the currently available captured value",
                    http_status=409,
                    details={"availableAmount": available, "currency": "INR"},
                )
            resource_id = new_uuid()
            operation = _new_operation(
                organisation_id=organisation_id,
                payment=payment,
                resource_id=resource_id,
                resource_type="REFUND",
                kind="REFUND",
                stable_provider_key=f"refund:ref_{resource_id.hex}",
            )
            refund = Refund(
                id=resource_id,
                public_id=f"ref_{resource_id.hex}",
                organisation_id=organisation_id,
                payment_intent_id=payment.id,
                capture_id=capture.id,
                provider_operation_id=operation.id,
                merchant_refund_reference=payload.merchant_refund_reference,
                amount=payload.amount,
                currency="INR",
                status="PROCESSING",
            )
            session.add_all([operation, refund])
            session.flush([operation, refund])
            return _attach_key(
                session,
                organisation_id=organisation_id,
                operation=operation,
                resource_public_id=refund.public_id,
                idempotency_key=idempotency_key,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )
    except RelayPayError:
        raise
    except IntegrityError as error:
        return _recover_operation_race(
            factory,
            organisation_id=organisation_id,
            key_digest=key_digest,
            fingerprint=fingerprint,
            error=error,
        )


def _locked_payment(
    session: Session, organisation_id: uuid.UUID, payment_public_id: str
) -> PaymentIntent:
    payment = session.scalar(
        select(PaymentIntent)
        .where(
            PaymentIntent.organisation_id == organisation_id,
            PaymentIntent.public_id == payment_public_id,
        )
        .with_for_update()
    )
    if payment is None:
        raise not_found("Payment intent")
    return payment


def _lock_operation_then_resource(
    session: Session,
    operation_id: uuid.UUID,
    resource_model: type[Authorization] | type[Capture],
) -> ProviderOperation:
    operation = session.scalar(
        select(ProviderOperation).where(ProviderOperation.id == operation_id).with_for_update()
    )
    if operation is None:
        raise RuntimeError("singleton resource has no provider operation")
    session.scalar(
        select(resource_model).where(resource_model.id == operation.resource_id).with_for_update()
    )
    return operation


def _recover_operation_race(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    key_digest: bytes,
    fingerprint: Fingerprint,
    error: IntegrityError,
) -> HTTPResult:
    with factory() as session, session.begin():
        winner = _existing_key(session, organisation_id=organisation_id, key_digest=key_digest)
        if winner is not None:
            return _existing_operation_result(session, record=winner, fingerprint=fingerprint)
    raise RelayPayError(
        code="COMMAND_CONFLICT",
        message="A concurrent command selected another durable winner",
        http_status=409,
    ) from error


def read_payment(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    payment_public_id: str,
) -> HTTPResult:
    with factory() as session, session.begin():
        payment = session.scalar(
            select(PaymentIntent).where(
                PaymentIntent.organisation_id == organisation_id,
                PaymentIntent.public_id == payment_public_id,
            )
        )
        if payment is None:
            raise not_found("Payment intent")
        authorization = session.scalar(
            select(Authorization).where(
                Authorization.organisation_id == organisation_id,
                Authorization.payment_intent_id == payment.id,
            )
        )
        capture = session.scalar(
            select(Capture).where(
                Capture.organisation_id == organisation_id,
                Capture.payment_intent_id == payment.id,
            )
        )
        refunds = session.scalars(
            select(Refund).where(
                Refund.organisation_id == organisation_id,
                Refund.payment_intent_id == payment.id,
            )
        ).all()
        reserved = sum(
            refund.amount
            for refund in refunds
            if refund.status in {"PROCESSING", "REQUIRES_REVIEW", "SUCCEEDED"}
        )
        captured_amount = capture.amount if capture and capture.status == "SUCCEEDED" else 0
        body = canonical_json_bytes(
            {
                "id": payment.public_id,
                "merchantReference": payment.merchant_reference,
                "amount": payment.amount,
                "currency": payment.currency,
                "authorizationStatus": authorization.status if authorization else None,
                "captureStatus": capture.status if capture else None,
                "capturedAmount": captured_amount,
                "reservedOrRefundedAmount": reserved,
                "refundableAmount": max(0, captured_amount - reserved),
            }
        )
        return HTTPResult(200, body, {"Content-Type": "application/json"})


def read_operation(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    operation_public_id: str,
) -> HTTPResult:
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(
                ProviderOperation.organisation_id == organisation_id,
                ProviderOperation.public_id == operation_public_id,
            )
        )
        if operation is None:
            raise not_found("Provider operation")
        return _operation_envelope(operation, _resource_public_id(session, operation))
