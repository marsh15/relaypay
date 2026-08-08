import hashlib
import html
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from relaypay.agent_runtime.models import ApprovalRequest, WorkflowRun
from relaypay.agent_runtime.workflows import resolve_admin_scope, store_artifact
from relaypay.disputes.models import (
    DisputeCase,
    DisputeDraftVersion,
    DisputePackageVersion,
    DisputeSubmissionAttempt,
)
from relaypay.disputes.package import PackageFile, freeze_package
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.security import Principal
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import PaymentIntent

REASON_CODES = {
    "FRAUD",
    "PRODUCT_NOT_RECEIVED",
    "NOT_AS_DESCRIBED",
    "DUPLICATE",
    "CREDIT_NOT_PROCESSED",
    "OTHER",
}


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_type: str = Field(alias="recordType", min_length=1, max_length=64)
    record_id: str = Field(alias="recordId", min_length=1, max_length=128)
    field_paths: list[str] = Field(alias="fieldPaths", min_length=1, max_length=32)
    snapshot_sha256: str = Field(alias="snapshotSha256", pattern=r"^[0-9a-f]{64}$")


class StructuredDisputeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    classification: str = Field(min_length=1, max_length=64)
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    missing_evidence: list[str] = Field(alias="missingEvidence", max_length=32)
    response_text: str = Field(alias="responseText", min_length=1, max_length=20_000)
    citations: list[Citation] = Field(min_length=1, max_length=64)


@dataclass(frozen=True, slots=True)
class NetworkObservation:
    status: Literal["SUCCEEDED", "FAILED", "UNKNOWN"]
    code: str
    response_bytes: bytes


class DisputeNetwork(Protocol):
    def submit(self, *, stable_key: str, package_bytes: bytes) -> NetworkObservation: ...

    def lookup(self, *, stable_key: str) -> NetworkObservation: ...


def open_case(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    payment_public_id: str,
    workflow_run_id: uuid.UUID,
    network_dispute_id: str,
    reason_code: str,
    amount: int,
    due_at: datetime,
    source_snapshot: dict[str, object],
) -> DisputeCase:
    if reason_code not in REASON_CODES:
        raise ValueError("unsupported dispute reason code")
    existing = session.scalar(
        select(DisputeCase).where(
            DisputeCase.organisation_id == organisation_id,
            DisputeCase.environment_id == environment_id,
            DisputeCase.network_dispute_id == network_dispute_id,
        )
    )
    if existing is not None:
        return existing
    payment = session.scalar(
        select(PaymentIntent).where(
            PaymentIntent.organisation_id == organisation_id,
            PaymentIntent.environment_id == environment_id,
            PaymentIntent.public_id == payment_public_id,
        )
    )
    if payment is None:
        raise not_found("Payment intent")
    snapshot_bytes = canonical_json_bytes(source_snapshot)
    item = DisputeCase(
        id=new_uuid(),
        public_id=new_public_id("dpc"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        payment_intent_id=payment.id,
        workflow_run_id=workflow_run_id,
        network_dispute_id=network_dispute_id,
        reason_code=reason_code,
        amount=amount,
        currency="INR",
        due_at=due_at,
        source_snapshot=source_snapshot,
        source_sha256=hashlib.sha256(snapshot_bytes).digest(),
        status="OPEN",
    )
    session.add(item)
    return item


def _case_for_admin(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    case_public_id: str,
    permission: str,
) -> DisputeCase:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission=permission,
    )
    case = session.scalar(
        select(DisputeCase).where(
            DisputeCase.organisation_id == organisation_id,
            DisputeCase.environment_id == environment_id,
            DisputeCase.public_id == case_public_id,
        )
    )
    if case is None:
        raise not_found("Dispute case")
    return case


def list_cases(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    limit: int = 50,
) -> list[DisputeCase]:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission="financial:read",
    )
    return list(
        session.scalars(
            select(DisputeCase)
            .where(
                DisputeCase.organisation_id == organisation_id,
                DisputeCase.environment_id == environment_id,
            )
            .order_by(DisputeCase.created_at.desc(), DisputeCase.id.desc())
            .limit(limit)
        ).all()
    )


def read_case(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    case_public_id: str,
    permission: str = "financial:read",
) -> tuple[DisputeCase, list[DisputeDraftVersion], list[DisputePackageVersion]]:
    case = _case_for_admin(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        case_public_id=case_public_id,
        permission=permission,
    )
    drafts = list(
        session.scalars(
            select(DisputeDraftVersion)
            .where(DisputeDraftVersion.dispute_case_id == case.id)
            .order_by(DisputeDraftVersion.version.desc())
        ).all()
    )
    packages = list(
        session.scalars(
            select(DisputePackageVersion)
            .where(DisputePackageVersion.dispute_case_id == case.id)
            .order_by(DisputePackageVersion.version.desc())
        ).all()
    )
    return case, drafts, packages


def latest_draft_for_admin(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    case_public_id: str,
) -> tuple[DisputeCase, DisputeDraftVersion]:
    case = _case_for_admin(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        case_public_id=case_public_id,
        permission="workflows:write",
    )
    draft = session.scalar(
        select(DisputeDraftVersion)
        .where(DisputeDraftVersion.dispute_case_id == case.id)
        .order_by(DisputeDraftVersion.version.desc())
        .limit(1)
    )
    if draft is None:
        raise not_found("Dispute draft")
    return case, draft


def read_package_for_admin(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    package_public_id: str,
    permission: str = "financial:read",
) -> DisputePackageVersion:
    organisation_id, environment_id = resolve_admin_scope(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        permission=permission,
    )
    package = session.scalar(
        select(DisputePackageVersion).where(
            DisputePackageVersion.organisation_id == organisation_id,
            DisputePackageVersion.environment_id == environment_id,
            DisputePackageVersion.public_id == package_public_id,
        )
    )
    if package is None:
        raise not_found("Dispute package")
    return package


def create_draft(
    session: Session,
    *,
    case: DisputeCase,
    draft: StructuredDisputeDraft,
    author_type: Literal["AGENT", "ANALYST"],
    author_user_id: uuid.UUID | None,
) -> DisputeDraftVersion:
    expected_snapshot = case.source_sha256.hex()
    snapshot_fields = set(case.source_snapshot) | {"$"}
    if any(
        item.snapshot_sha256 != expected_snapshot
        or item.record_type != "dispute_case_snapshot"
        or item.record_id != case.public_id
        or not set(item.field_paths) <= snapshot_fields
        for item in draft.citations
    ):
        raise RelayPayError(
            code="INVALID_CITATION",
            message="Citation does not reference the immutable case snapshot",
            http_status=422,
        )
    previous = session.scalar(
        select(DisputeDraftVersion)
        .where(DisputeDraftVersion.dispute_case_id == case.id)
        .order_by(DisputeDraftVersion.version.desc())
        .limit(1)
    )
    content = draft.model_dump(mode="json", by_alias=True)
    item = DisputeDraftVersion(
        id=new_uuid(),
        public_id=new_public_id("dpd"),
        organisation_id=case.organisation_id,
        environment_id=case.environment_id,
        dispute_case_id=case.id,
        version=1 if previous is None else previous.version + 1,
        author_type=author_type,
        author_user_id=author_user_id,
        classification=draft.classification,
        confidence=draft.confidence,
        response_text=draft.response_text,
        selected_evidence=[item.model_dump(mode="json", by_alias=True) for item in draft.citations],
        missing_evidence=draft.missing_evidence,
        content_sha256=hashlib.sha256(canonical_json_bytes(content)).digest(),
        supersedes_id=None if previous is None else previous.id,
    )
    if previous is not None:
        session.execute(
            update(DisputePackageVersion)
            .where(
                DisputePackageVersion.dispute_case_id == case.id,
                DisputePackageVersion.status.in_(("FROZEN", "APPROVED")),
            )
            .values(status="INVALIDATED")
        )
        artifact_ids = select(DisputePackageVersion.workflow_artifact_id).where(
            DisputePackageVersion.dispute_case_id == case.id
        )
        session.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.artifact_id.in_(artifact_ids),
                ApprovalRequest.status.in_(("PENDING", "APPROVED")),
            )
            .values(status="INVALIDATED")
        )
    case.status = "DRAFTED"
    session.add(item)
    return item


def freeze_draft(
    session: Session,
    *,
    case: DisputeCase,
    draft: DisputeDraftVersion,
    attachments: tuple[PackageFile, ...],
    signing_secret: bytes,
    maker_user_id: uuid.UUID,
) -> tuple[DisputePackageVersion, ApprovalRequest]:
    if draft.dispute_case_id != case.id or case.workflow_run_id is None:
        raise ValueError("draft and workflow must belong to the dispute case")
    run = session.get(WorkflowRun, case.workflow_run_id)
    if run is None:
        raise not_found("Workflow run")
    rendered = (
        "<!doctype html><html><body><h1>Dispute response</h1><p>"
        + html.escape(draft.response_text)
        + "</p></body></html>"
    ).encode()
    frozen = freeze_package(
        dispute_id=case.public_id,
        draft_id=draft.public_id,
        response_html=rendered,
        attachments=attachments,
        signing_secret=signing_secret,
    )
    manifest_bytes = canonical_json_bytes(frozen.manifest)
    artifact = store_artifact(
        session,
        run=run,
        artifact_type="DISPUTE_PACKAGE_MANIFEST",
        media_type="application/json",
        content=manifest_bytes,
        creator_user_id=maker_user_id,
    )
    session.flush([artifact])
    previous_version = session.scalar(
        select(DisputePackageVersion.version)
        .where(DisputePackageVersion.dispute_case_id == case.id)
        .order_by(DisputePackageVersion.version.desc())
        .limit(1)
    )
    package = DisputePackageVersion(
        id=new_uuid(),
        public_id=new_public_id("dpp"),
        organisation_id=case.organisation_id,
        environment_id=case.environment_id,
        dispute_case_id=case.id,
        draft_version_id=draft.id,
        workflow_artifact_id=artifact.id,
        version=(previous_version or 0) + 1,
        manifest=frozen.manifest,
        package_bytes=frozen.content,
        package_sha256=frozen.sha256,
        byte_length=len(frozen.content),
        status="FROZEN",
    )
    approval = ApprovalRequest(
        id=new_uuid(),
        public_id=new_public_id("apr"),
        organisation_id=case.organisation_id,
        environment_id=case.environment_id,
        workflow_run_id=run.id,
        artifact_id=artifact.id,
        artifact_sha256=frozen.sha256,
        maker_user_id=maker_user_id,
        status="PENDING",
    )
    case.status = "WAITING_FOR_APPROVAL"
    session.add_all([package, approval])
    return package, approval


def mark_package_approved(
    session: Session, *, package_public_id: str, approval: ApprovalRequest
) -> DisputePackageVersion:
    package = session.scalar(
        select(DisputePackageVersion).where(
            DisputePackageVersion.public_id == package_public_id,
            DisputePackageVersion.workflow_artifact_id == approval.artifact_id,
            DisputePackageVersion.package_sha256 == approval.artifact_sha256,
            DisputePackageVersion.status == "FROZEN",
        )
    )
    if package is None or approval.status != "APPROVED":
        raise RelayPayError(
            code="PACKAGE_NOT_APPROVED", message="Exact package approval required", http_status=409
        )
    case = session.get(DisputeCase, package.dispute_case_id)
    if case is None:
        raise not_found("Dispute case")
    package.status = "APPROVED"
    case.status = "APPROVED"
    return package


def submit_approved_package(
    factory: sessionmaker[Session],
    *,
    package_public_id: str,
    network: DisputeNetwork,
    now: datetime | None = None,
) -> DisputeSubmissionAttempt:
    timestamp = now or datetime.now(UTC)
    with factory() as session, session.begin():
        package = session.scalar(
            select(DisputePackageVersion)
            .where(DisputePackageVersion.public_id == package_public_id)
            .with_for_update()
        )
        if package is None:
            raise not_found("Dispute package")
        case = session.get(DisputeCase, package.dispute_case_id)
        if case is None or package.status not in {"APPROVED", "SUBMITTED"}:
            raise RelayPayError(
                code="PACKAGE_NOT_APPROVED",
                message="Exact package approval required",
                http_status=409,
            )
        attempt = session.scalar(
            select(DisputeSubmissionAttempt)
            .where(DisputeSubmissionAttempt.package_version_id == package.id)
            .order_by(DisputeSubmissionAttempt.attempt_number.desc())
            .limit(1)
        )
        is_new_attempt = attempt is None
        if is_new_attempt:
            stable_key = f"dispute:{case.public_id}:{package.package_sha256.hex()}"
            attempt = DisputeSubmissionAttempt(
                id=new_uuid(),
                public_id=new_public_id("dps"),
                organisation_id=case.organisation_id,
                environment_id=case.environment_id,
                dispute_case_id=case.id,
                package_version_id=package.id,
                attempt_number=1,
                stable_key=stable_key,
                request_sha256=package.package_sha256,
                status="SENT",
                sent_at=timestamp,
            )
            session.add(attempt)
            case.status = "SUBMITTING"
        assert attempt is not None
        stable_key, package_bytes, attempt_id = (
            attempt.stable_key,
            package.package_bytes,
            attempt.id,
        )
        prior_status = attempt.status
    if prior_status == "SUCCEEDED":
        return attempt
    try:
        observation = (
            network.submit(stable_key=stable_key, package_bytes=package_bytes)
            if is_new_attempt
            else network.lookup(stable_key=stable_key)
        )
    except (ConnectionError, TimeoutError):
        observation = NetworkObservation("UNKNOWN", "NETWORK_AMBIGUOUS", b"")
    with factory() as session, session.begin():
        persisted = session.get(DisputeSubmissionAttempt, attempt_id)
        if persisted is None:
            raise not_found("Dispute submission attempt")
        case = session.get(DisputeCase, persisted.dispute_case_id)
        package = session.get(DisputePackageVersion, persisted.package_version_id)
        persisted.response_code = observation.code
        persisted.response_sha256 = hashlib.sha256(observation.response_bytes).digest()
        if observation.status == "SUCCEEDED":
            persisted.status = "SUCCEEDED"
            persisted.completed_at = timestamp
            if case is not None:
                case.status = "SUBMITTED"
                case.submitted_at = timestamp
            if package is not None:
                package.status = "SUBMITTED"
        elif observation.status == "FAILED":
            persisted.status = "FAILED"
            persisted.completed_at = timestamp
            if case is not None:
                case.status = "REQUIRES_REVIEW"
        else:
            persisted.status = "AMBIGUOUS"
        return persisted
