import hashlib
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.agent_runtime.events import record_consumption
from relaypay.agent_runtime.models import WorkflowDefinition, WorkflowRun
from relaypay.disputes.models import DisputeCase
from relaypay.disputes.service import open_case
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment, Organisation
from relaypay.ids import new_public_id, new_uuid


class DisputeCreatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    payment_id: str = Field(alias="paymentId", min_length=1, max_length=64)
    network_dispute_id: str = Field(alias="networkDisputeId", min_length=1, max_length=128)
    reason_code: str = Field(alias="reasonCode", min_length=1, max_length=32)
    amount: int = Field(gt=0)
    due_at: AwareDatetime = Field(alias="dueAt")
    source_snapshot: dict[str, object] = Field(alias="sourceSnapshot")


class DisputeCreatedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(alias="eventId", pattern=r"^bev_[0-9a-f]{32}$")
    event_type: str = Field(alias="eventType")
    schema_version: int = Field(alias="schemaVersion")
    occurred_at: AwareDatetime = Field(alias="occurredAt")
    organisation_id: str = Field(alias="organisationId")
    environment_id: str = Field(alias="environmentId")
    resource_type: str = Field(alias="resourceType")
    resource_id: str = Field(alias="resourceId")
    payload: DisputeCreatedPayload
    payload_sha256: str = Field(alias="payloadSha256", pattern=r"^[0-9a-f]{64}$")


def consume_dispute_created(session: Session, envelope: DisputeCreatedEnvelope) -> DisputeCase:
    if envelope.event_type != "dispute.created.v1" or envelope.schema_version != 1:
        raise RelayPayError(
            code="UNSUPPORTED_EVENT",
            message="Expected dispute.created.v1 schema 1",
            http_status=422,
        )
    payload_bytes = canonical_json_bytes(envelope.payload.model_dump(mode="json", by_alias=True))
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
    if not record_consumption(
        session,
        organisation_id=organisation.id,
        environment_id=environment.id,
        consumer_name="dispute-response-agent-v1",
        event_id=envelope.event_id,
        payload_sha256=payload_digest,
    ):
        existing = session.scalar(
            select(DisputeCase).where(
                DisputeCase.organisation_id == organisation.id,
                DisputeCase.environment_id == environment.id,
                DisputeCase.network_dispute_id == envelope.payload.network_dispute_id,
            )
        )
        if existing is None:
            raise RelayPayError(
                code="EVENT_REPLAY_CONFLICT",
                message="Consumed event has no matching dispute case",
                http_status=409,
            )
        return existing
    definition = session.scalar(
        select(WorkflowDefinition)
        .where(
            WorkflowDefinition.organisation_id == organisation.id,
            WorkflowDefinition.environment_id == environment.id,
            WorkflowDefinition.name == "dispute-response",
            WorkflowDefinition.status == "ACTIVE",
        )
        .order_by(WorkflowDefinition.version.desc())
        .limit(1)
    )
    if definition is None:
        raise not_found("Dispute response workflow definition")
    run = WorkflowRun(
        id=new_uuid(),
        public_id=new_public_id("wfr"),
        organisation_id=organisation.id,
        environment_id=environment.id,
        workflow_definition_id=definition.id,
        trigger_event_id=envelope.event_id,
        route="EVENT:dispute.created.v1",
        idempotency_digest=hashlib.sha256(envelope.event_id.encode()).digest(),
        status="RUNNING",
        token_budget=10_000,
        cost_budget_usd_micros=100_000,
        tokens_used=0,
        cost_used_usd_micros=0,
    )
    session.add(run)
    return open_case(
        session,
        organisation_id=organisation.id,
        environment_id=environment.id,
        payment_public_id=envelope.payload.payment_id,
        workflow_run_id=run.id,
        network_dispute_id=envelope.payload.network_dispute_id,
        reason_code=envelope.payload.reason_code,
        amount=envelope.payload.amount,
        due_at=datetime.fromisoformat(envelope.payload.due_at.isoformat()),
        source_snapshot=envelope.payload.source_snapshot,
    )
