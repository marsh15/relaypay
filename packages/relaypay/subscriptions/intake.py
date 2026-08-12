import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.agent_runtime.events import record_consumption
from relaypay.agent_runtime.models import WorkflowDefinition, WorkflowRun
from relaypay.agent_runtime.security import tokenize_pii
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment, Organisation
from relaypay.ids import new_public_id, new_uuid
from relaypay.subscriptions.models import (
    RecoveryCase,
    RecurringPaymentAttempt,
    Subscription,
    SubscriptionInvoice,
)
from relaypay.subscriptions.service import (
    ensure_default_policy,
    open_recovery_case,
    record_payment_attempt,
)


class RecurringPaymentFailedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subscription_id: str = Field(alias="subscriptionId", pattern=r"^sub_[0-9a-f]{32}$")
    invoice_id: str = Field(alias="invoiceId", pattern=r"^inv_[0-9a-f]{32}$")
    provider_attempt_id: str = Field(alias="providerAttemptId", min_length=1, max_length=128)
    provider_code: str = Field(alias="providerCode", min_length=1, max_length=64)
    outcome: Literal["VERIFIED_FAILED", "TRANSPORT_UNKNOWN"]
    evidence: dict[str, object]


class RecurringPaymentFailedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(alias="eventId", pattern=r"^bev_[0-9a-f]{32}$")
    event_type: str = Field(alias="eventType")
    schema_version: int = Field(alias="schemaVersion")
    occurred_at: AwareDatetime = Field(alias="occurredAt")
    organisation_id: str = Field(alias="organisationId", pattern=r"^org_[0-9a-f]{32}$")
    environment_id: str = Field(alias="environmentId", pattern=r"^env_[0-9a-f]{32}$")
    resource_type: str = Field(alias="resourceType")
    resource_id: str = Field(alias="resourceId")
    payload: RecurringPaymentFailedPayload
    payload_sha256: str = Field(alias="payloadSha256", pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RecoveryIntakeResult:
    attempt: RecurringPaymentAttempt
    case: RecoveryCase | None


def assert_pii_free_evidence(value: object, *, depth: int = 0) -> None:
    if depth > 6:
        raise RelayPayError(
            code="EVENT_EVIDENCE_TOO_DEEP",
            message="Recurring payment evidence exceeds nesting limit",
            http_status=422,
        )
    if isinstance(value, str):
        if tokenize_pii(value).values:
            raise RelayPayError(
                code="EVENT_CONTAINS_PII",
                message="Recurring payment event evidence must be tokenized",
                http_status=422,
            )
        return
    if isinstance(value, list):
        for item in value:
            assert_pii_free_evidence(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        blocked = {"name", "email", "phone", "address", "account", "messageid"}
        for key, item in value.items():
            if any(token in key.casefold().replace("_", "") for token in blocked):
                raise RelayPayError(
                    code="EVENT_CONTAINS_PII",
                    message="Recurring payment event evidence contains a forbidden field",
                    http_status=422,
                )
            assert_pii_free_evidence(item, depth=depth + 1)


def consume_recurring_payment_failed(
    session: Session, envelope: RecurringPaymentFailedEnvelope
) -> RecoveryIntakeResult:
    if envelope.event_type != "recurring-payment.failed.v1" or envelope.schema_version != 1:
        raise RelayPayError(
            code="UNSUPPORTED_EVENT",
            message="Expected recurring-payment.failed.v1 schema 1",
            http_status=422,
        )
    payload_bytes = canonical_json_bytes(envelope.payload.model_dump(mode="json", by_alias=True))
    if len(payload_bytes) > 65_536:
        raise RelayPayError(
            code="EVENT_TOO_LARGE",
            message="Recurring payment event exceeds 64 KiB",
            http_status=413,
        )
    assert_pii_free_evidence(envelope.payload.evidence)
    payload_digest = hashlib.sha256(payload_bytes).digest()
    if payload_digest.hex() != envelope.payload_sha256:
        raise RelayPayError(
            code="EVENT_DIGEST_MISMATCH", message="Event payload digest mismatch", http_status=422
        )
    scope = session.execute(
        select(Organisation, Environment)
        .join(Environment, Environment.organisation_id == Organisation.id)
        .where(
            Organisation.public_id == envelope.organisation_id,
            Environment.public_id == envelope.environment_id,
            Organisation.status == "ACTIVE",
            Environment.status == "ACTIVE",
        )
    ).one_or_none()
    if scope is None:
        raise not_found("Event scope")
    organisation, environment = scope
    subscription = session.scalar(
        select(Subscription).where(
            Subscription.organisation_id == organisation.id,
            Subscription.environment_id == environment.id,
            Subscription.public_id == envelope.payload.subscription_id,
        )
    )
    if subscription is None:
        raise not_found("Subscription")
    invoice = session.scalar(
        select(SubscriptionInvoice).where(
            SubscriptionInvoice.organisation_id == organisation.id,
            SubscriptionInvoice.environment_id == environment.id,
            SubscriptionInvoice.subscription_id == subscription.id,
            SubscriptionInvoice.public_id == envelope.payload.invoice_id,
        )
    )
    if invoice is None:
        raise not_found("Invoice")
    if not record_consumption(
        session,
        organisation_id=organisation.id,
        environment_id=environment.id,
        consumer_name="subscription-recovery-agent-v1",
        event_id=envelope.event_id,
        payload_sha256=payload_digest,
    ):
        attempt = session.scalar(
            select(RecurringPaymentAttempt).where(
                RecurringPaymentAttempt.provider_attempt_id == envelope.payload.provider_attempt_id
            )
        )
        if attempt is None:
            raise RelayPayError(
                code="EVENT_REPLAY_CONFLICT",
                message="Consumed event has no matching recurring attempt",
                http_status=409,
            )
        case = session.scalar(select(RecoveryCase).where(RecoveryCase.invoice_id == invoice.id))
        return RecoveryIntakeResult(attempt, case)
    attempt = record_payment_attempt(
        session,
        invoice=invoice,
        provider_attempt_id=envelope.payload.provider_attempt_id,
        provider_code=envelope.payload.provider_code,
        outcome=envelope.payload.outcome,
        occurred_at=datetime.fromisoformat(envelope.occurred_at.isoformat()),
        evidence=envelope.payload.evidence,
    )
    session.flush([attempt])
    if envelope.payload.outcome == "TRANSPORT_UNKNOWN":
        return RecoveryIntakeResult(attempt, None)
    definition = session.scalar(
        select(WorkflowDefinition)
        .where(
            WorkflowDefinition.organisation_id == organisation.id,
            WorkflowDefinition.environment_id == environment.id,
            WorkflowDefinition.name == "subscription-recovery",
            WorkflowDefinition.status == "ACTIVE",
        )
        .order_by(WorkflowDefinition.version.desc())
        .limit(1)
    )
    if definition is None:
        raise not_found("Subscription recovery workflow definition")
    run = WorkflowRun(
        id=new_uuid(),
        public_id=new_public_id("wfr"),
        organisation_id=organisation.id,
        environment_id=environment.id,
        workflow_definition_id=definition.id,
        trigger_event_id=envelope.event_id,
        route="EVENT:recurring-payment.failed.v1",
        idempotency_digest=hashlib.sha256(envelope.event_id.encode()).digest(),
        status="RUNNING",
        token_budget=6_000,
        cost_budget_usd_micros=60_000,
        tokens_used=0,
        cost_used_usd_micros=0,
    )
    session.add(run)
    session.flush([run])
    policy = ensure_default_policy(
        session, organisation_id=organisation.id, environment_id=environment.id
    )
    session.flush([policy])
    case = open_recovery_case(
        session,
        subscription=subscription,
        invoice=invoice,
        trigger_attempt=attempt,
        workflow_run=run,
        policy=policy,
        now=datetime.fromisoformat(envelope.occurred_at.isoformat()),
    )
    return RecoveryIntakeResult(attempt, case)
