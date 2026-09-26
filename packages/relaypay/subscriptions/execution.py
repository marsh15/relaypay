import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_public_id, new_uuid
from relaypay.subscriptions.models import (
    CommunicationRecord,
    RecoveryCase,
    RecurringPaymentAttempt,
    ScheduledRecoveryAction,
    Subscription,
    SubscriptionInvoice,
)
from relaypay.subscriptions.network import RecoveryNetworkObservation
from relaypay.subscriptions.policy import classify_provider_code
from relaypay.subscriptions.service import TERMINAL_CASE_STATES, terminate_case


class CommunicationNetwork(Protocol):
    def send(self, *, stable_key: str, channel: str, body: str) -> RecoveryNetworkObservation: ...

    def lookup(self, *, stable_key: str) -> RecoveryNetworkObservation: ...


class RecurringPaymentNetwork(Protocol):
    def retry(
        self, *, stable_key: str, invoice_id: str, amount: int
    ) -> RecoveryNetworkObservation: ...

    def lookup(self, *, stable_key: str) -> RecoveryNetworkObservation: ...


@dataclass(frozen=True, slots=True)
class PreparedAction:
    action_id: object
    case_id: object
    stable_key: str
    mode: str
    channel: str | None
    body: str | None
    invoice_public_id: str
    amount: int


def _render_message(subscription: Subscription, invoice: SubscriptionInvoice) -> tuple[str, str]:
    display_name = subscription.consent.get("displayName", "Synthetic customer")
    if not isinstance(display_name, str) or not 1 <= len(display_name) <= 128:
        raise ValueError("synthetic display name is invalid")
    tokenized = (
        "Hello <CUSTOMER_NAME>, synthetic invoice <INVOICE_ID> needs payment-method attention. "
        "No real payment or customer data is involved."
    )
    rendered = tokenized.replace("<CUSTOMER_NAME>", display_name).replace(
        "<INVOICE_ID>", invoice.public_id
    )
    return tokenized, rendered


def _prepare_action(
    session: Session, *, action_public_id: str, expected_type: str, now: datetime
) -> PreparedAction:
    action = session.scalar(
        select(ScheduledRecoveryAction)
        .where(ScheduledRecoveryAction.public_id == action_public_id)
        .with_for_update()
    )
    if action is None:
        raise not_found("Scheduled recovery action")
    if action.action_type != expected_type:
        raise RelayPayError(
            code="RECOVERY_ACTION_TYPE_MISMATCH",
            message="Scheduled action has a different type",
            http_status=409,
        )
    case = session.scalar(select(RecoveryCase).where(RecoveryCase.id == action.recovery_case_id))
    if case is None:
        raise not_found("Recovery case")
    subscription = session.get(Subscription, case.subscription_id)
    invoice = session.get(SubscriptionInvoice, case.invoice_id)
    if subscription is None or invoice is None:
        raise not_found("Recovery evidence")
    if case.status in TERMINAL_CASE_STATES:
        raise RelayPayError(
            code="RECOVERY_CASE_TERMINAL",
            message="No action is allowed after recovery terminates",
            http_status=409,
        )
    if now >= case.expires_at:
        raise RelayPayError(
            code="RECOVERY_POLICY_EXPIRED",
            message="Recovery policy window has expired",
            http_status=409,
        )
    if action.scheduled_for > now:
        raise RelayPayError(
            code="RECOVERY_ACTION_NOT_DUE",
            message="Scheduled recovery action is not due",
            http_status=409,
        )
    if action.status in {"EXECUTED", "CANCELLED", "SUPPRESSED"}:
        return PreparedAction(
            action.id,
            case.id,
            action.stable_key,
            "COMPLETE",
            action.channel,
            None,
            invoice.public_id,
            invoice.amount,
        )
    mode = "LOOKUP" if action.status in {"EXECUTING", "AMBIGUOUS"} else "SEND"
    action.status = "EXECUTING"
    body: str | None = None
    if expected_type == "MESSAGE":
        if action.channel is None:
            raise ValueError("message action requires a channel")
        tokenized, body = _render_message(subscription, invoice)
        record = session.scalar(
            select(CommunicationRecord).where(CommunicationRecord.scheduled_action_id == action.id)
        )
        if record is None:
            if mode == "LOOKUP":
                raise RelayPayError(
                    code="COMMUNICATION_EVIDENCE_MISSING",
                    message="Sent message has no immutable attempt evidence",
                    http_status=409,
                )
            request_bytes = canonical_json_bytes(
                {"stableKey": action.stable_key, "channel": action.channel, "body": body}
            )
            session.add(
                CommunicationRecord(
                    id=new_uuid(),
                    public_id=new_public_id("com"),
                    organisation_id=case.organisation_id,
                    environment_id=case.environment_id,
                    recovery_case_id=case.id,
                    scheduled_action_id=action.id,
                    stable_key=action.stable_key,
                    channel=action.channel,
                    tokenized_body=tokenized,
                    rendered_body=body,
                    request_sha256=hashlib.sha256(request_bytes).digest(),
                    status="SENT",
                )
            )
        else:
            body = record.rendered_body
    return PreparedAction(
        action.id,
        case.id,
        action.stable_key,
        mode,
        action.channel,
        body,
        invoice.public_id,
        invoice.amount,
    )


def _safe_network_call(call: object) -> RecoveryNetworkObservation:
    try:
        if not callable(call):
            raise TypeError("network operation is not callable")
        value = call()
        if not isinstance(value, RecoveryNetworkObservation):
            raise TypeError("network returned an invalid observation")
        return value
    except (TimeoutError, ConnectionError):
        return RecoveryNetworkObservation("UNKNOWN", "TRANSPORT_UNKNOWN", b"")


def execute_message_action(
    factory: sessionmaker[Session],
    *,
    action_public_id: str,
    network: CommunicationNetwork,
    now: datetime | None = None,
) -> ScheduledRecoveryAction:
    timestamp = now or datetime.now(UTC)
    with factory() as session, session.begin():
        prepared = _prepare_action(
            session, action_public_id=action_public_id, expected_type="MESSAGE", now=timestamp
        )
    if prepared.mode == "COMPLETE":
        with factory() as session:
            item = session.get(ScheduledRecoveryAction, prepared.action_id)
            if item is None:
                raise not_found("Scheduled recovery action")
            return item
    if prepared.channel is None or prepared.body is None:
        raise ValueError("prepared message is incomplete")
    observation = _safe_network_call(
        lambda: (
            network.lookup(stable_key=prepared.stable_key)
            if prepared.mode == "LOOKUP"
            else network.send(
                stable_key=prepared.stable_key,
                channel=prepared.channel or "",
                body=prepared.body or "",
            )
        )
    )
    with factory() as session, session.begin():
        action = session.get(ScheduledRecoveryAction, prepared.action_id)
        case = session.get(RecoveryCase, prepared.case_id)
        if action is None or case is None:
            raise not_found("Recovery action")
        record = session.scalar(
            select(CommunicationRecord).where(CommunicationRecord.scheduled_action_id == action.id)
        )
        if record is None:
            raise not_found("Communication record")
        digest = hashlib.sha256(observation.response_bytes).digest()
        action.response_code = observation.code
        action.response_sha256 = digest
        record.response_code = observation.code
        record.response_sha256 = digest
        if observation.status == "UNKNOWN":
            action.status = "AMBIGUOUS"
            record.status = "AMBIGUOUS"
        elif observation.status == "FAILED":
            action.status = "EXECUTED"
            action.executed_at = timestamp
            record.status = "FAILED"
        else:
            action.status = "EXECUTED"
            action.executed_at = timestamp
            record.status = "DELIVERED"
            case.message_count += 1
        return action


def execute_payment_action(
    factory: sessionmaker[Session],
    *,
    action_public_id: str,
    network: RecurringPaymentNetwork,
    now: datetime | None = None,
) -> ScheduledRecoveryAction:
    timestamp = now or datetime.now(UTC)
    with factory() as session, session.begin():
        prepared = _prepare_action(
            session,
            action_public_id=action_public_id,
            expected_type="PAYMENT_RETRY",
            now=timestamp,
        )
    if prepared.mode == "COMPLETE":
        with factory() as session:
            item = session.get(ScheduledRecoveryAction, prepared.action_id)
            if item is None:
                raise not_found("Scheduled recovery action")
            return item
    observation = _safe_network_call(
        lambda: (
            network.lookup(stable_key=prepared.stable_key)
            if prepared.mode == "LOOKUP"
            else network.retry(
                stable_key=prepared.stable_key,
                invoice_id=prepared.invoice_public_id,
                amount=prepared.amount,
            )
        )
    )
    with factory() as session, session.begin():
        action = session.get(ScheduledRecoveryAction, prepared.action_id)
        case = session.get(RecoveryCase, prepared.case_id)
        if action is None or case is None:
            raise not_found("Recovery action")
        invoice = session.get(SubscriptionInvoice, case.invoice_id)
        if invoice is None:
            raise not_found("Subscription invoice")
        digest = hashlib.sha256(observation.response_bytes).digest()
        action.response_code = observation.code
        action.response_sha256 = digest
        if observation.status == "UNKNOWN":
            action.status = "AMBIGUOUS"
            return action
        action.status = "EXECUTED"
        action.executed_at = timestamp
        case.payment_retry_count += 1
        classification = (
            None if observation.status == "SUCCEEDED" else classify_provider_code(observation.code)
        )
        previous_number = session.scalar(
            select(RecurringPaymentAttempt.attempt_number)
            .where(RecurringPaymentAttempt.invoice_id == invoice.id)
            .order_by(RecurringPaymentAttempt.attempt_number.desc())
            .limit(1)
        )
        attempt = RecurringPaymentAttempt(
            id=new_uuid(),
            public_id=new_public_id("rpa"),
            organisation_id=case.organisation_id,
            environment_id=case.environment_id,
            invoice_id=invoice.id,
            attempt_number=(previous_number or 0) + 1,
            provider_attempt_id=prepared.stable_key,
            outcome=(
                "VERIFIED_SUCCEEDED" if observation.status == "SUCCEEDED" else "VERIFIED_FAILED"
            ),
            failure_classification=classification,
            provider_code=observation.code,
            evidence={"stableKey": prepared.stable_key, "responseSha256": digest.hex()},
            evidence_sha256=hashlib.sha256(
                canonical_json_bytes(
                    {"stableKey": prepared.stable_key, "responseSha256": digest.hex()}
                )
            ).digest(),
            occurred_at=timestamp,
        )
        session.add(attempt)
        if observation.status == "SUCCEEDED":
            invoice.status = "PAID"
            invoice.paid_at = timestamp
            terminate_case(
                session, case=case, reason="PAYMENT_SUCCEEDED", now=timestamp, recovered=True
            )
        elif classification == "NON_RETRYABLE_HARD_DECLINE":
            terminate_case(session, case=case, reason="NON_RETRYABLE_FAILURE", now=timestamp)
        elif case.payment_retry_count >= 3:
            terminate_case(session, case=case, reason="PAYMENT_RETRIES_EXHAUSTED", now=timestamp)
        return action


def run_recovery_action_batch(
    factory: sessionmaker[Session],
    *,
    communication_network: CommunicationNetwork,
    payment_network: RecurringPaymentNetwork,
    now: datetime | None = None,
    limit: int = 10,
) -> int:
    timestamp = now or datetime.now(UTC)
    processed = 0
    while processed < limit:
        with factory() as session, session.begin():
            candidate = session.execute(
                select(ScheduledRecoveryAction, RecoveryCase)
                .join(RecoveryCase, RecoveryCase.id == ScheduledRecoveryAction.recovery_case_id)
                .where(
                    ScheduledRecoveryAction.status.in_(("SCHEDULED", "AMBIGUOUS", "EXECUTING")),
                    ScheduledRecoveryAction.scheduled_for <= timestamp,
                    ~RecoveryCase.status.in_(TERMINAL_CASE_STATES),
                )
                .order_by(
                    ScheduledRecoveryAction.scheduled_for,
                    ScheduledRecoveryAction.created_at,
                    ScheduledRecoveryAction.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            ).one_or_none()
            if candidate is not None:
                action, case = candidate
                if timestamp >= case.expires_at:
                    terminate_case(session, case=case, reason="POLICY_EXPIRED", now=timestamp)
                    case.status = "EXPIRED"
                    processed += 1
                    continue
        if candidate is None:
            break
        public_id, action_type = action.public_id, action.action_type
        try:
            if action_type == "MESSAGE":
                execute_message_action(
                    factory,
                    action_public_id=public_id,
                    network=communication_network,
                    now=timestamp,
                )
            else:
                execute_payment_action(
                    factory,
                    action_public_id=public_id,
                    network=payment_network,
                    now=timestamp,
                )
        except RelayPayError:
            # A racing writer terminated or expired the case between the
            # committed pre-check and the execute transaction; the action's
            # own transaction rolled back untouched. Consume a batch slot for
            # the skip: terminal and expired cases are excluded by the next
            # selection, so this is bounded and the batch keeps moving.
            processed += 1
            continue
        processed += 1
    return processed
