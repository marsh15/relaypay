import hashlib
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from relaypay.agent_runtime.models import WorkflowRun
from relaypay.agent_runtime.workflows import resolve_admin_scope
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.security import Principal
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import Customer
from relaypay.subscriptions.models import (
    RecoveryCase,
    RecoveryOptOut,
    RecoveryPolicyVersion,
    RecurringPaymentAttempt,
    ScheduledRecoveryAction,
    Subscription,
    SubscriptionInvoice,
)
from relaypay.subscriptions.policy import (
    FailureClassification,
    PolicyDefinition,
    classify_provider_code,
    propose_actions,
)

TERMINAL_CASE_STATES = frozenset({"RECOVERED", "TERMINATED", "EXPIRED"})


def create_subscription(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    customer_public_id: str,
    external_id: str,
    plan_reference: str,
    amount: int,
    consent: dict[str, object],
) -> Subscription:
    consent_bytes = canonical_json_bytes(consent)
    consent_digest = hashlib.sha256(consent_bytes).digest()
    existing = session.scalar(
        select(Subscription).where(
            Subscription.organisation_id == organisation_id,
            Subscription.environment_id == environment_id,
            Subscription.external_id == external_id,
        )
    )
    if existing is not None:
        if (
            existing.plan_reference != plan_reference
            or existing.amount != amount
            or existing.consent_sha256 != consent_digest
        ):
            raise RelayPayError(
                code="SUBSCRIPTION_SOURCE_CONFLICT",
                message="Subscription identifier was reused with different content",
                http_status=409,
            )
        return existing
    customer = session.scalar(
        select(Customer).where(
            Customer.organisation_id == organisation_id,
            Customer.environment_id == environment_id,
            Customer.public_id == customer_public_id,
        )
    )
    if customer is None:
        raise not_found("Customer")
    item = Subscription(
        id=new_uuid(),
        public_id=new_public_id("sub"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        customer_id=customer.id,
        external_id=external_id,
        plan_reference=plan_reference,
        amount=amount,
        currency="INR",
        consent=consent,
        consent_sha256=consent_digest,
        status="ACTIVE",
    )
    session.add(item)
    return item


def create_invoice(
    session: Session,
    *,
    subscription: Subscription,
    external_id: str,
    due_at: datetime,
) -> SubscriptionInvoice:
    existing = session.scalar(
        select(SubscriptionInvoice).where(
            SubscriptionInvoice.subscription_id == subscription.id,
            SubscriptionInvoice.external_id == external_id,
        )
    )
    if existing is not None:
        if existing.due_at != due_at or existing.amount != subscription.amount:
            raise RelayPayError(
                code="INVOICE_SOURCE_CONFLICT",
                message="Invoice identifier was reused with different content",
                http_status=409,
            )
        return existing
    item = SubscriptionInvoice(
        id=new_uuid(),
        public_id=new_public_id("inv"),
        organisation_id=subscription.organisation_id,
        environment_id=subscription.environment_id,
        subscription_id=subscription.id,
        external_id=external_id,
        amount=subscription.amount,
        currency=subscription.currency,
        due_at=due_at,
        status="OPEN",
    )
    session.add(item)
    return item


def ensure_default_policy(
    session: Session, *, organisation_id: uuid.UUID, environment_id: uuid.UUID
) -> RecoveryPolicyVersion:
    existing = session.scalar(
        select(RecoveryPolicyVersion).where(
            RecoveryPolicyVersion.organisation_id == organisation_id,
            RecoveryPolicyVersion.environment_id == environment_id,
            RecoveryPolicyVersion.status == "ACTIVE",
        )
    )
    if existing is not None:
        return existing
    definition = PolicyDefinition()
    value = {
        "maxPaymentRetries": definition.max_payment_retries,
        "maxMessages": definition.max_messages,
        "windowDays": definition.window_days,
        "retryWindowsHours": list(definition.retry_windows_hours),
        "messageWindowsHours": list(definition.message_windows_hours),
    }
    item = RecoveryPolicyVersion(
        id=new_uuid(),
        public_id=new_public_id("rpv"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        version=1,
        max_payment_retries=definition.max_payment_retries,
        max_messages=definition.max_messages,
        window_days=definition.window_days,
        retry_windows_hours=list(definition.retry_windows_hours),
        message_windows_hours=list(definition.message_windows_hours),
        policy_sha256=hashlib.sha256(canonical_json_bytes(value)).digest(),
        status="ACTIVE",
    )
    session.add(item)
    return item


def policy_definition(policy: RecoveryPolicyVersion) -> PolicyDefinition:
    return PolicyDefinition(
        max_payment_retries=policy.max_payment_retries,
        max_messages=policy.max_messages,
        window_days=policy.window_days,
        retry_windows_hours=tuple(policy.retry_windows_hours),
        message_windows_hours=tuple(policy.message_windows_hours),
    )


def record_payment_attempt(
    session: Session,
    *,
    invoice: SubscriptionInvoice,
    provider_attempt_id: str,
    provider_code: str,
    outcome: str,
    occurred_at: datetime,
    evidence: dict[str, object],
) -> RecurringPaymentAttempt:
    digest = hashlib.sha256(canonical_json_bytes(evidence)).digest()
    existing = session.scalar(
        select(RecurringPaymentAttempt).where(
            RecurringPaymentAttempt.provider_attempt_id == provider_attempt_id
        )
    )
    if existing is not None:
        if existing.evidence_sha256 != digest:
            raise RelayPayError(
                code="RECURRING_ATTEMPT_CONFLICT",
                message="Provider attempt was reused with different evidence",
                http_status=409,
            )
        return existing
    classification = classify_provider_code(provider_code)
    if outcome not in {"VERIFIED_FAILED", "VERIFIED_SUCCEEDED", "TRANSPORT_UNKNOWN"}:
        raise ValueError("unsupported recurring payment outcome")
    previous = session.scalar(
        select(RecurringPaymentAttempt.attempt_number)
        .where(RecurringPaymentAttempt.invoice_id == invoice.id)
        .order_by(RecurringPaymentAttempt.attempt_number.desc())
        .limit(1)
    )
    item = RecurringPaymentAttempt(
        id=new_uuid(),
        public_id=new_public_id("rpa"),
        organisation_id=invoice.organisation_id,
        environment_id=invoice.environment_id,
        invoice_id=invoice.id,
        attempt_number=(previous or 0) + 1,
        provider_attempt_id=provider_attempt_id,
        outcome=outcome,
        failure_classification=classification,
        provider_code=provider_code,
        evidence=evidence,
        evidence_sha256=digest,
        occurred_at=occurred_at,
    )
    invoice.status = "PAID" if outcome == "VERIFIED_SUCCEEDED" else "FAILED"
    if outcome == "VERIFIED_SUCCEEDED":
        invoice.paid_at = occurred_at
    session.add(item)
    return item


def open_recovery_case(
    session: Session,
    *,
    subscription: Subscription,
    invoice: SubscriptionInvoice,
    trigger_attempt: RecurringPaymentAttempt,
    workflow_run: WorkflowRun,
    policy: RecoveryPolicyVersion,
    now: datetime,
) -> RecoveryCase:
    existing = session.scalar(select(RecoveryCase).where(RecoveryCase.invoice_id == invoice.id))
    if existing is not None:
        return existing
    if trigger_attempt.outcome == "TRANSPORT_UNKNOWN":
        raise RelayPayError(
            code="AMBIGUOUS_RECURRING_PAYMENT",
            message="Unknown provider outcomes require lookup and cannot start recovery",
            http_status=409,
        )
    if trigger_attempt.outcome != "VERIFIED_FAILED":
        raise RelayPayError(
            code="RECOVERY_NOT_REQUIRED",
            message="Only a verified failed payment can start recovery",
            http_status=409,
        )
    if trigger_attempt.failure_classification is None:
        raise ValueError("failed payment attempt requires a failure classification")
    classification: FailureClassification = trigger_attempt.failure_classification  # type: ignore[assignment]
    hard_stop = classification == "NON_RETRYABLE_HARD_DECLINE"
    item = RecoveryCase(
        id=new_uuid(),
        public_id=new_public_id("rcy"),
        organisation_id=subscription.organisation_id,
        environment_id=subscription.environment_id,
        subscription_id=subscription.id,
        invoice_id=invoice.id,
        trigger_attempt_id=trigger_attempt.id,
        policy_version_id=policy.id,
        workflow_run_id=workflow_run.id,
        status="TERMINATED" if hard_stop else "OPEN",
        classification=classification,
        payment_retry_count=0,
        message_count=0,
        started_at=now,
        expires_at=now + timedelta(days=policy.window_days),
        terminated_at=now if hard_stop else None,
        termination_reason="NON_RETRYABLE_FAILURE" if hard_stop else None,
    )
    subscription.status = "PAST_DUE"
    session.add(item)
    session.flush([item])
    if hard_stop:
        workflow_run.status = "CANCELLED"
        workflow_run.completed_at = now
        return item
    proposed = propose_actions(
        classification=classification,
        consent=subscription.consent,
        started_at=now,
        policy=policy_definition(policy),
    )
    for sequence, action in enumerate(proposed, start=1):
        payload = {
            "caseId": item.public_id,
            "invoiceId": invoice.public_id,
            "actionType": action.action_type,
            "channel": action.channel,
        }
        session.add(
            ScheduledRecoveryAction(
                id=new_uuid(),
                public_id=new_public_id("rsa"),
                organisation_id=item.organisation_id,
                environment_id=item.environment_id,
                recovery_case_id=item.id,
                sequence=sequence,
                action_type=action.action_type,
                channel=action.channel,
                scheduled_for=action.scheduled_for,
                stable_key=f"recovery:{item.public_id}:action:{sequence}",
                payload=payload,
                payload_sha256=hashlib.sha256(canonical_json_bytes(payload)).digest(),
                status="SCHEDULED",
            )
        )
    item.status = "SCHEDULED" if proposed else "TERMINATED"
    if not proposed:
        item.terminated_at = now
        item.termination_reason = "NO_CONSENTED_ACTION"
        workflow_run.status = "CANCELLED"
        workflow_run.completed_at = now
    return item


def terminate_case(
    session: Session, *, case: RecoveryCase, reason: str, now: datetime, recovered: bool = False
) -> None:
    if case.status in TERMINAL_CASE_STATES:
        return
    case.status = "RECOVERED" if recovered else "TERMINATED"
    case.terminated_at = now
    case.termination_reason = reason
    session.execute(
        update(ScheduledRecoveryAction)
        .where(
            ScheduledRecoveryAction.recovery_case_id == case.id,
            ScheduledRecoveryAction.status.in_(("SCHEDULED", "EXECUTING", "AMBIGUOUS")),
        )
        .values(status="CANCELLED")
    )
    workflow = session.get(WorkflowRun, case.workflow_run_id)
    if workflow is not None:
        workflow.status = "SUCCEEDED" if recovered else "CANCELLED"
        workflow.completed_at = now
    subscription = session.get(Subscription, case.subscription_id)
    if subscription is not None and recovered:
        subscription.status = "RECOVERED"


def record_opt_out(
    session: Session,
    *,
    case: RecoveryCase,
    source_event_id: str,
    channel: str,
    received_at: datetime,
) -> RecoveryOptOut:
    existing = session.scalar(
        select(RecoveryOptOut).where(RecoveryOptOut.source_event_id == source_event_id)
    )
    if existing is not None:
        return existing
    if channel not in {"ALL", "EMAIL", "WHATSAPP", "IN_APP"}:
        raise ValueError("unsupported opt-out channel")
    item = RecoveryOptOut(
        id=new_uuid(),
        public_id=new_public_id("opt"),
        organisation_id=case.organisation_id,
        environment_id=case.environment_id,
        recovery_case_id=case.id,
        source_event_id=source_event_id,
        channel=channel,
        received_at=received_at,
    )
    session.add(item)
    terminate_case(session, case=case, reason="CUSTOMER_OPT_OUT", now=received_at)
    return item


def list_recovery_cases(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    limit: int = 50,
) -> list[RecoveryCase]:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission="financial:read",
    )
    return list(
        session.scalars(
            select(RecoveryCase)
            .where(
                RecoveryCase.organisation_id == organisation_id,
                RecoveryCase.environment_id == environment_id,
            )
            .order_by(RecoveryCase.created_at.desc(), RecoveryCase.id.desc())
            .limit(limit)
        ).all()
    )


def read_recovery_case(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    case_public_id: str,
) -> tuple[RecoveryCase, Subscription, SubscriptionInvoice, list[ScheduledRecoveryAction]]:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission="financial:read",
    )
    case = session.scalar(
        select(RecoveryCase).where(
            RecoveryCase.organisation_id == organisation_id,
            RecoveryCase.environment_id == environment_id,
            RecoveryCase.public_id == case_public_id,
        )
    )
    if case is None:
        raise not_found("Recovery case")
    subscription = session.get(Subscription, case.subscription_id)
    invoice = session.get(SubscriptionInvoice, case.invoice_id)
    if subscription is None or invoice is None:
        raise not_found("Recovery evidence")
    actions = list(
        session.scalars(
            select(ScheduledRecoveryAction)
            .where(ScheduledRecoveryAction.recovery_case_id == case.id)
            .order_by(ScheduledRecoveryAction.sequence)
        ).all()
    )
    return case, subscription, invoice, actions
