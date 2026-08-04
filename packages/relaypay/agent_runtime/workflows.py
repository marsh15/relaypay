import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.agent_runtime.models import (
    ApprovalDecision,
    ApprovalRequest,
    DeadLetter,
    WorkflowArtifact,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment
from relaypay.identity.security import Principal
from relaypay.ids import new_public_id, new_uuid

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_PACKAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StepLease:
    step_id: uuid.UUID
    lease_token: uuid.UUID
    definition: dict[str, object]


def _require_scope(principal: Principal, permission: str) -> tuple[uuid.UUID, uuid.UUID]:
    if permission not in principal.scopes or principal.environment_id is None:
        raise RelayPayError(code="FORBIDDEN", message="Permission denied", http_status=403)
    return principal.organisation_id, principal.environment_id


def resolve_admin_scope(
    session: Session, *, principal: Principal, environment_public_id: str, permission: str
) -> tuple[uuid.UUID, uuid.UUID]:
    if permission not in principal.scopes:
        raise RelayPayError(code="FORBIDDEN", message="Permission denied", http_status=403)
    environment_id = session.scalar(
        select(Environment.id).where(
            Environment.organisation_id == principal.organisation_id,
            Environment.public_id == environment_public_id,
            Environment.status == "ACTIVE",
        )
    )
    if environment_id is None:
        raise not_found("Environment")
    return principal.organisation_id, environment_id


def list_runs(
    session: Session, *, principal: Principal, environment_public_id: str, limit: int = 50
) -> list[WorkflowRun]:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission="technical:read",
    )
    return list(
        session.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.organisation_id == organisation_id,
                WorkflowRun.environment_id == environment_id,
            )
            .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
            .limit(limit)
        ).all()
    )


def read_run(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    run_public_id: str,
) -> tuple[
    WorkflowRun,
    list[WorkflowStep],
    list[WorkflowArtifact],
    list[ApprovalRequest],
    list[DeadLetter],
]:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission="technical:read",
    )
    run = session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.organisation_id == organisation_id,
            WorkflowRun.environment_id == environment_id,
            WorkflowRun.public_id == run_public_id,
        )
    )
    if run is None:
        raise not_found("Workflow run")
    steps = list(
        session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_run_id == run.id)
            .order_by(WorkflowStep.created_at, WorkflowStep.id)
        ).all()
    )
    artifacts = list(
        session.scalars(
            select(WorkflowArtifact)
            .where(WorkflowArtifact.workflow_run_id == run.id)
            .order_by(WorkflowArtifact.created_at, WorkflowArtifact.id)
        ).all()
    )
    dead_letters = list(
        session.scalars(
            select(DeadLetter)
            .where(DeadLetter.workflow_run_id == run.id)
            .order_by(DeadLetter.created_at, DeadLetter.id)
        ).all()
    )
    approvals = list(
        session.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.workflow_run_id == run.id)
            .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
        ).all()
    )
    return run, steps, artifacts, approvals, dead_letters


def activate_definition(
    session: Session,
    *,
    principal: Principal,
    name: str,
    definition: dict[str, object],
) -> WorkflowDefinition:
    organisation_id, environment_id = _require_scope(principal, "workflows:write")
    previous = session.scalar(
        select(WorkflowDefinition)
        .where(
            WorkflowDefinition.organisation_id == organisation_id,
            WorkflowDefinition.environment_id == environment_id,
            WorkflowDefinition.name == name,
        )
        .order_by(WorkflowDefinition.version.desc())
        .limit(1)
    )
    version = 1 if previous is None else previous.version + 1
    if previous is not None:
        previous.status = "RETIRED"
    value = canonical_json_bytes(definition)
    item = WorkflowDefinition(
        id=new_uuid(),
        public_id=new_public_id("wdf"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        name=name,
        version=version,
        definition_sha256=hashlib.sha256(value).digest(),
        definition=definition,
        status="ACTIVE",
    )
    session.add(item)
    return item


def start_run(
    session: Session,
    *,
    principal: Principal,
    definition_public_id: str,
    route: str,
    idempotency_key: str,
    token_budget: int,
    cost_budget_usd_micros: int,
    now: datetime | None = None,
) -> WorkflowRun:
    organisation_id, environment_id = _require_scope(principal, "workflows:write")
    digest = hashlib.sha256(f"{route}:{idempotency_key}".encode()).digest()
    existing = session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.organisation_id == organisation_id,
            WorkflowRun.environment_id == environment_id,
            WorkflowRun.route == route,
            WorkflowRun.idempotency_digest == digest,
        )
    )
    if existing is not None:
        return existing
    definition = session.scalar(
        select(WorkflowDefinition).where(
            WorkflowDefinition.organisation_id == organisation_id,
            WorkflowDefinition.environment_id == environment_id,
            WorkflowDefinition.public_id == definition_public_id,
            WorkflowDefinition.status == "ACTIVE",
        )
    )
    if definition is None:
        raise not_found("Workflow definition")
    run = WorkflowRun(
        id=new_uuid(),
        public_id=new_public_id("wfr"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        workflow_definition_id=definition.id,
        route=route,
        idempotency_digest=digest,
        status="QUEUED",
        token_budget=token_budget,
        cost_budget_usd_micros=cost_budget_usd_micros,
        tokens_used=0,
        cost_used_usd_micros=0,
    )
    session.add(run)
    timestamp = now or datetime.now(UTC)
    steps = definition.definition.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("workflow steps must be a list")
    for raw in steps:
        if not isinstance(raw, dict):
            raise ValueError("workflow step must be an object")
        kind = str(raw.get("kind", "SYSTEM"))
        session.add(
            WorkflowStep(
                id=new_uuid(),
                public_id=new_public_id("wfs"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                workflow_run_id=run.id,
                step_key=str(raw["key"]),
                step_kind=kind,
                definition=raw,
                status="QUEUED",
                attempt_count=0,
                max_attempts=3 if kind == "MODEL" else 5,
                next_attempt_at=timestamp,
            )
        )
    return run


def claim_step(session: Session, *, now: datetime, lease_seconds: int = 60) -> StepLease | None:
    step = session.scalar(
        select(WorkflowStep)
        .join(WorkflowRun, WorkflowRun.id == WorkflowStep.workflow_run_id)
        .where(
            WorkflowStep.status.in_(("QUEUED", "WAITING_UNTIL", "RUNNING")),
            WorkflowStep.next_attempt_at <= now,
            (WorkflowStep.lease_expires_at.is_(None)) | (WorkflowStep.lease_expires_at <= now),
            WorkflowRun.cancellation_requested_at.is_(None),
        )
        .order_by(WorkflowStep.next_attempt_at, WorkflowStep.created_at, WorkflowStep.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if step is None:
        return None
    token = new_uuid()
    step.status = "RUNNING"
    step.attempt_count += 1
    step.lease_token = token
    step.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return StepLease(step.id, token, step.definition)


def complete_step(
    session: Session,
    *,
    lease: StepLease,
    output: dict[str, object],
    tokens_used: int = 0,
    cost_usd_micros: int = 0,
    now: datetime | None = None,
) -> WorkflowStep:
    step = session.scalar(
        select(WorkflowStep).where(
            WorkflowStep.id == lease.step_id, WorkflowStep.lease_token == lease.lease_token
        )
    )
    if step is None:
        raise RelayPayError(
            code="LEASE_LOST", message="Step lease is no longer valid", http_status=409
        )
    run = session.get(WorkflowRun, step.workflow_run_id)
    if run is None:
        raise not_found("Workflow run")
    if run.tokens_used + tokens_used > run.token_budget or (
        run.cost_used_usd_micros + cost_usd_micros > run.cost_budget_usd_micros
    ):
        step.status = "REQUIRES_REVIEW"
        step.safe_error_code = "MODEL_BUDGET_EXHAUSTED"
        run.status = "REQUIRES_REVIEW"
    else:
        run.tokens_used += tokens_used
        run.cost_used_usd_micros += cost_usd_micros
        step.status = "SUCCEEDED"
        step.output = output
        step.completed_at = now or datetime.now(UTC)
    step.lease_token = None
    step.lease_expires_at = None
    return step


def fail_step(
    session: Session,
    *,
    lease: StepLease,
    reason_code: str,
    retryable: bool,
    now: datetime,
) -> WorkflowStep:
    step = session.scalar(
        select(WorkflowStep).where(
            WorkflowStep.id == lease.step_id, WorkflowStep.lease_token == lease.lease_token
        )
    )
    if step is None:
        raise RelayPayError(
            code="LEASE_LOST", message="Step lease is no longer valid", http_status=409
        )
    step.lease_token = None
    step.lease_expires_at = None
    step.safe_error_code = reason_code
    if retryable and step.attempt_count < step.max_attempts:
        jitter = (
            int(
                hashlib.sha256(f"{step.public_id}:{step.attempt_count}".encode()).hexdigest()[:2],
                16,
            )
            % 7
        )
        step.status = "WAITING_UNTIL"
        step.next_attempt_at = now + timedelta(seconds=min(300, 2**step.attempt_count) + jitter)
        return step
    step.status = "DEAD_LETTER"
    session.add(
        DeadLetter(
            id=new_uuid(),
            public_id=new_public_id("dlq"),
            organisation_id=step.organisation_id,
            environment_id=step.environment_id,
            workflow_run_id=step.workflow_run_id,
            workflow_step_id=step.id,
            reason_code=reason_code,
            evidence={"attemptCount": step.attempt_count, "stepId": step.public_id},
            replay_count=0,
        )
    )
    return step


def store_artifact(
    session: Session,
    *,
    run: WorkflowRun,
    artifact_type: str,
    media_type: str,
    content: bytes,
    creator_user_id: uuid.UUID | None,
) -> WorkflowArtifact:
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact exceeds 5 MiB")
    current_bytes = sum(
        session.scalars(
            select(WorkflowArtifact.byte_length).where(WorkflowArtifact.workflow_run_id == run.id)
        ).all()
    )
    if current_bytes + len(content) > MAX_PACKAGE_BYTES:
        raise ValueError("assembled package exceeds 20 MiB")
    latest = session.scalar(
        select(WorkflowArtifact.version)
        .where(
            WorkflowArtifact.workflow_run_id == run.id,
            WorkflowArtifact.artifact_type == artifact_type,
        )
        .order_by(WorkflowArtifact.version.desc())
        .limit(1)
    )
    artifact = WorkflowArtifact(
        id=new_uuid(),
        public_id=new_public_id("art"),
        organisation_id=run.organisation_id,
        environment_id=run.environment_id,
        workflow_run_id=run.id,
        artifact_type=artifact_type,
        version=(latest or 0) + 1,
        media_type=media_type,
        content_bytes=content,
        content_sha256=hashlib.sha256(content).digest(),
        byte_length=len(content),
        created_by_user_id=creator_user_id,
    )
    session.add(artifact)
    return artifact


def decide_approval(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    request_public_id: str,
    decision: str,
    note: str | None,
) -> ApprovalDecision:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission="approvals:write",
    )
    request = session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.public_id == request_public_id,
            ApprovalRequest.organisation_id == organisation_id,
            ApprovalRequest.environment_id == environment_id,
            ApprovalRequest.status == "PENDING",
        )
    )
    if request is None:
        raise not_found("Approval request")
    if principal.user_id is None or principal.user_id == request.maker_user_id:
        raise RelayPayError(
            code="MAKER_CHECKER_REQUIRED",
            message="A maker cannot approve their own artifact",
            http_status=403,
        )
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("invalid approval decision")
    item = ApprovalDecision(
        id=new_uuid(),
        public_id=new_public_id("apd"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        approval_request_id=request.id,
        maker_user_id=request.maker_user_id,
        decision_maker_user_id=principal.user_id,
        decision=decision,
        note=note,
    )
    request.status = decision
    session.add(item)
    return item
