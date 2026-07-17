import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.event_delivery.models import (
    EventRecipient,
    MerchantEvent,
    WebhookDelivery,
    WebhookDeliveryAttempt,
)
from relaypay.ledger.models import Journal, Posting
from relaypay.payments.models import Authorization, Capture, PaymentIntent, Refund
from relaypay.provider_operations.models import (
    IdempotencyRecord,
    OperationHistory,
    ProviderAttempt,
    ProviderOperation,
)

MAX_EVIDENCE_ROWS = 100


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _digest(value: bytes | None) -> str | None:
    return value.hex() if value else None


def payment_evidence(
    session: Session, *, organisation_id: uuid.UUID, payment_public_id: str
) -> dict[str, Any] | None:
    payment = session.scalar(
        select(PaymentIntent).where(
            PaymentIntent.organisation_id == organisation_id,
            PaymentIntent.public_id == payment_public_id,
        )
    )
    if payment is None:
        return None
    operations = list(
        session.scalars(
            select(ProviderOperation)
            .where(
                ProviderOperation.organisation_id == organisation_id,
                ProviderOperation.payment_intent_id == payment.id,
            )
            .order_by(ProviderOperation.created_at, ProviderOperation.id)
            .limit(MAX_EVIDENCE_ROWS)
        )
    )
    operation_ids = [item.id for item in operations]
    attempts = (
        list(
            session.scalars(
                select(ProviderAttempt)
                .where(
                    ProviderAttempt.organisation_id == organisation_id,
                    ProviderAttempt.provider_operation_id.in_(operation_ids),
                )
                .order_by(ProviderAttempt.created_at, ProviderAttempt.id)
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if operation_ids
        else []
    )
    histories = (
        list(
            session.scalars(
                select(OperationHistory)
                .where(
                    OperationHistory.organisation_id == organisation_id,
                    OperationHistory.provider_operation_id.in_(operation_ids),
                )
                .order_by(OperationHistory.created_at, OperationHistory.id)
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if operation_ids
        else []
    )
    events = list(
        session.scalars(
            select(MerchantEvent)
            .where(
                MerchantEvent.organisation_id == organisation_id,
                MerchantEvent.payment_intent_id == payment.id,
            )
            .order_by(MerchantEvent.occurred_at, MerchantEvent.id)
            .limit(MAX_EVIDENCE_ROWS)
        )
    )
    event_ids = [item.id for item in events]
    recipients = (
        list(
            session.scalars(
                select(EventRecipient)
                .where(
                    EventRecipient.organisation_id == organisation_id,
                    EventRecipient.merchant_event_id.in_(event_ids),
                )
                .order_by(EventRecipient.created_at, EventRecipient.id)
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if event_ids
        else []
    )
    recipient_ids = [item.id for item in recipients]
    deliveries = (
        list(
            session.scalars(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.organisation_id == organisation_id,
                    WebhookDelivery.event_recipient_id.in_(recipient_ids),
                )
                .order_by(WebhookDelivery.created_at, WebhookDelivery.id)
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if recipient_ids
        else []
    )
    delivery_ids = [item.id for item in deliveries]
    delivery_attempts = (
        list(
            session.scalars(
                select(WebhookDeliveryAttempt)
                .where(
                    WebhookDeliveryAttempt.organisation_id == organisation_id,
                    WebhookDeliveryAttempt.webhook_delivery_id.in_(delivery_ids),
                )
                .order_by(WebhookDeliveryAttempt.created_at, WebhookDeliveryAttempt.id)
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if delivery_ids
        else []
    )
    resources: list[Any] = []
    for model in (Authorization, Capture, Refund):
        resources.extend(
            session.scalars(
                select(model)
                .where(
                    model.organisation_id == organisation_id,
                    model.payment_intent_id == payment.id,
                )
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
    journals = (
        list(
            session.scalars(
                select(Journal)
                .where(
                    Journal.organisation_id == organisation_id,
                    Journal.provider_operation_id.in_(operation_ids),
                )
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if operation_ids
        else []
    )
    journal_ids = [item.id for item in journals]
    postings = (
        list(
            session.scalars(
                select(Posting)
                .where(
                    Posting.organisation_id == organisation_id,
                    Posting.journal_id.in_(journal_ids),
                )
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if journal_ids
        else []
    )
    idempotency = (
        list(
            session.scalars(
                select(IdempotencyRecord)
                .where(
                    IdempotencyRecord.organisation_id == organisation_id,
                    IdempotencyRecord.provider_operation_id.in_(operation_ids),
                )
                .limit(MAX_EVIDENCE_ROWS)
            )
        )
        if operation_ids
        else []
    )
    return {
        "paymentIntent": {
            "id": payment.public_id,
            "merchantReference": payment.merchant_reference,
            "amount": payment.amount,
            "currency": payment.currency,
            "createdAt": _time(payment.created_at),
        },
        "resources": [
            {
                "id": item.public_id,
                "type": item.__class__.__name__.upper(),
                "status": item.status,
                "amount": item.amount,
                "currency": item.currency,
            }
            for item in resources
        ],
        "idempotency": [
            {
                "keyHint": item.key_hint,
                "fingerprintSummary": item.fingerprint_summary,
                "isTerminal": item.is_terminal,
                "responseSha256": _digest(item.response_sha256),
            }
            for item in idempotency
        ],
        "providerOperations": [
            {
                "id": item.public_id,
                "kind": item.kind,
                "stableProviderKey": item.stable_provider_key,
                "status": item.status,
                "attemptCount": item.attempt_count,
                "requestSha256": _digest(item.provider_request_sha256),
                "responseSha256": _digest(item.terminal_response_sha256),
                "finalizedAt": _time(item.finalized_at),
            }
            for item in operations
        ],
        "providerAttempts": [
            {
                "operationId": str(item.provider_operation_id),
                "sequence": item.sequence,
                "kind": item.attempt_kind,
                "state": item.state,
                "requestSha256": _digest(item.request_sha256),
                "responseSha256": _digest(item.response_sha256),
                "httpStatus": item.response_http_status,
                "classification": item.classification,
                "safeErrorCode": item.safe_error_code,
            }
            for item in attempts
        ],
        "operationHistory": [
            {
                "operationId": str(item.provider_operation_id),
                "from": item.from_status,
                "to": item.to_status,
                "reason": item.reason_code,
                "actor": item.actor_type,
                "correlationId": item.correlation_id,
            }
            for item in histories
        ],
        "ledger": {
            "journals": [
                {"id": item.public_id, "type": item.journal_type, "currency": item.currency}
                for item in journals
            ],
            "postings": [
                {"journalId": str(item.journal_id), "side": item.side, "amount": item.amount}
                for item in postings
            ],
        },
        "events": [
            {"id": item.public_id, "type": item.event_type, "sha256": _digest(item.event_sha256)}
            for item in events
        ],
        "recipients": [
            {
                "id": str(item.id),
                "eventId": str(item.merchant_event_id),
                "endpointVersionId": str(item.endpoint_version_id),
            }
            for item in recipients
        ],
        "deliveries": [
            {
                "id": item.public_id,
                "status": item.status,
                "attemptCount": item.attempt_count,
                "deliveredAt": _time(item.delivered_at),
                "deadLetteredAt": _time(item.dead_lettered_at),
            }
            for item in deliveries
        ],
        "deliveryAttempts": [
            {
                "deliveryId": str(item.webhook_delivery_id),
                "sequence": item.sequence,
                "result": item.result,
                "eventSha256": _digest(item.event_sha256),
                "httpStatus": item.response_http_status,
                "safeErrorCode": item.safe_error_code,
            }
            for item in delivery_attempts
        ],
        "limits": {"perCollection": MAX_EVIDENCE_ROWS},
    }
