"""Merchant risk review orchestration.

Snapshots, reviews, versions, findings, scores, and escalations are immutable
once written. Human reviewers may annotate and disposition escalations but can
never rewrite evidence, findings, or calculated scores. Provider calls are
network I/O and always run outside database transactions.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.agent_runtime.contracts import ModelRequest, StructuredModelProvider
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_public_id, new_uuid
from relaypay.risk_review.checks import (
    CheckResult,
    ScoreBreakdown,
    claims_risk,
    deterministic_checks,
    escalation_reason,
    is_hard_stop,
    severity_band,
)
from relaypay.risk_review.findings import (
    ModelFinding,
    ModelFindings,
    findings_prompt,
    validate_model_findings,
)
from relaypay.risk_review.models import (
    OnboardingSnapshot,
    RiskAnnotation,
    RiskEscalation,
    RiskFinding,
    RiskReview,
    RiskReviewVersion,
)
from relaypay.risk_review.snapshot import SiteSnapshotSource


@dataclass(frozen=True, slots=True)
class PreparedReview:
    review_id: uuid.UUID
    review_public_id: str
    snapshot_id: uuid.UUID
    snapshot: dict[str, object]
    snapshot_sha256: bytes
    replayed: bool
    organisation_public_id: str
    environment_public_id: str


def store_snapshot(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    fetched: object,
    source_url: str,
) -> OnboardingSnapshot:
    from relaypay.risk_review.snapshot import SiteSnapshot

    if not isinstance(fetched, SiteSnapshot):
        raise TypeError("fetched must be a SiteSnapshot")
    digest = bytes.fromhex(fetched.snapshot_sha256)
    existing = session.scalar(
        select(OnboardingSnapshot).where(
            OnboardingSnapshot.organisation_id == organisation_id,
            OnboardingSnapshot.environment_id == environment_id,
            OnboardingSnapshot.site_ref == fetched.site_ref,
            OnboardingSnapshot.snapshot_sha256 == digest,
        )
    )
    if existing is not None:
        return existing
    item = OnboardingSnapshot(
        id=new_uuid(),
        public_id=new_public_id("osn"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        site_ref=fetched.site_ref,
        snapshot=fetched.snapshot,
        snapshot_sha256=digest,
        source_url=source_url,
    )
    session.add(item)
    session.flush([item])
    return item


def prepare_review(
    session_factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    organisation_public_id: str,
    environment_public_id: str,
    site_ref: str,
    source: SiteSnapshotSource,
    source_url: str,
) -> PreparedReview:
    """Fetch the snapshot (network I/O) and open the idempotent review shell."""
    fetched = source.fetch(site_ref)
    with session_factory() as session, session.begin():
        snapshot_row = store_snapshot(
            session,
            organisation_id=organisation_id,
            environment_id=environment_id,
            fetched=fetched,
            source_url=source_url,
        )
        existing = session.scalar(
            select(RiskReview).where(
                RiskReview.organisation_id == organisation_id,
                RiskReview.environment_id == environment_id,
                RiskReview.onboarding_snapshot_id == snapshot_row.id,
            )
        )
        if existing is not None:
            return PreparedReview(
                review_id=existing.id,
                review_public_id=existing.public_id,
                snapshot_id=snapshot_row.id,
                snapshot=snapshot_row.snapshot,
                snapshot_sha256=snapshot_row.snapshot_sha256,
                replayed=True,
                organisation_public_id=organisation_public_id,
                environment_public_id=environment_public_id,
            )
        review = RiskReview(
            id=new_uuid(),
            public_id=new_public_id("rsk"),
            organisation_id=organisation_id,
            environment_id=environment_id,
            onboarding_snapshot_id=snapshot_row.id,
            status="PENDING",
            score_version=1,
        )
        session.add(review)
        session.flush([review])
        return PreparedReview(
            review_id=review.id,
            review_public_id=review.public_id,
            snapshot_id=snapshot_row.id,
            snapshot=snapshot_row.snapshot,
            snapshot_sha256=snapshot_row.snapshot_sha256,
            replayed=False,
            organisation_public_id=organisation_public_id,
            environment_public_id=environment_public_id,
        )


def _add_finding(
    session: Session,
    *,
    review: RiskReview,
    position: int,
    check: CheckResult | None,
    model_finding: ModelFinding | None,
    snapshot_sha256: bytes,
    hard_stop: bool,
    confidence: str | None = None,
    severity: str | None = None,
) -> None:
    if check is not None:
        finding_type = "DETERMINISTIC"
        classification = check.classification
        resolved_severity = check.severity
        resolved_confidence = check.confidence
        detail = check.detail
        quote = None
        source_path = None
    elif model_finding is not None:
        finding_type = "MODEL"
        classification = model_finding.classification
        resolved_severity = severity or "MEDIUM"
        resolved_confidence = confidence or "LOW"
        detail = "Model-identified suspicious language or claim."
        quote = model_finding.quote
        source_path = model_finding.source_path
    else:
        raise ValueError("a finding requires a check or a model finding")
    severity = resolved_severity
    confidence = resolved_confidence
    session.add(
        RiskFinding(
            id=new_uuid(),
            public_id=new_public_id("rfd"),
            organisation_id=review.organisation_id,
            environment_id=review.environment_id,
            risk_review_id=review.id,
            position=position,
            finding_type=finding_type,
            check_key=(check.check_key if check is not None else "model_claims"),
            classification=classification,
            severity=severity,
            confidence=confidence,
            detail=detail,
            quote=quote,
            source_path=source_path,
            snapshot_sha256=snapshot_sha256,
            hard_stop=hard_stop,
        )
    )


def execute_review(
    session_factory: sessionmaker[Session],
    prepared: PreparedReview,
    *,
    provider: StructuredModelProvider,
    now: datetime,
) -> dict[str, object]:
    """Run deterministic checks, the model findings pass, score, and finalize."""
    if prepared.replayed:
        return read_review_payload(session_factory, prepared.review_public_id)
    model_result = provider.generate_structured(
        ModelRequest(
            prompt=findings_prompt(prepared.snapshot),
            schema=ModelFindings,
            model_id="risk-review-v1",
            max_output_tokens=2048,
            trace_id=prepared.review_public_id,
        )
    )
    raw_findings = model_result.output
    if not isinstance(raw_findings, ModelFindings):
        raise RelayPayError(
            code="RISK_FINDINGS_INVALID",
            message="Findings provider returned an unsupported schema",
            http_status=502,
        )
    findings = validate_model_findings(raw_findings, prepared.snapshot)
    with session_factory() as session, session.begin():
        review = session.get(RiskReview, prepared.review_id)
        if review is None:
            raise not_found("Risk review")
        checks, base = deterministic_checks(prepared.snapshot, now=now)
        deterministic_classifications = {item.classification for item in checks} - {"NONE"}
        hard_stop = False
        hard_stop_classification = "NONE"
        position = 1
        model_confidences: list[str] = []
        for check in checks:
            stop = is_hard_stop(check.classification)
            if stop:
                hard_stop = True
                hard_stop_classification = check.classification
            _add_finding(
                session,
                review=review,
                position=position,
                check=check,
                model_finding=None,
                snapshot_sha256=prepared.snapshot_sha256,
                hard_stop=stop,
            )
            position += 1
        for model_finding in findings:
            if model_finding.classification in deterministic_classifications:
                finding_confidence = "HIGH"
            elif model_finding.source_path == "html":
                finding_confidence = "LOW"
            else:
                finding_confidence = "MEDIUM"
            model_confidences.append(finding_confidence)
            stop = is_hard_stop(model_finding.classification) and finding_confidence != "LOW"
            if stop:
                hard_stop = True
                hard_stop_classification = model_finding.classification
            _add_finding(
                session,
                review=review,
                position=position,
                check=None,
                model_finding=model_finding,
                snapshot_sha256=prepared.snapshot_sha256,
                hard_stop=stop,
                confidence=finding_confidence,
                severity="HIGH" if stop else "MEDIUM",
            )
            position += 1
        # Deterministic checks already return risk contributions; model claims add
        # five points each, capped at 15.
        breakdown = ScoreBreakdown(
            completeness=base.completeness,
            domain=base.domain,
            category=base.category,
            pricing=base.pricing,
            claims=claims_risk(len(findings)),
        )
        severity = severity_band(breakdown.total)
        if model_confidences:
            confidence = (
                "HIGH"
                if "HIGH" in model_confidences
                else "MEDIUM"
                if "MEDIUM" in model_confidences
                else "LOW"
            )
        else:
            confidence = "HIGH"
        version_number = (
            session.scalar(
                select(RiskReviewVersion.version)
                .where(RiskReviewVersion.risk_review_id == review.id)
                .order_by(RiskReviewVersion.version.desc())
                .limit(1)
            )
            or 0
        ) + 1
        version_payload = {
            "scoreVersion": 1,
            "snapshotSha256": prepared.snapshot_sha256.hex(),
            "completeness": breakdown.completeness,
            "domain": breakdown.domain,
            "category": breakdown.category,
            "pricing": breakdown.pricing,
            "claims": breakdown.claims,
            "total": breakdown.total,
            "severity": severity,
            "confidence": confidence,
            "hardStop": hard_stop,
            "modelFindingCount": len(findings),
        }
        version = RiskReviewVersion(
            id=new_uuid(),
            public_id=new_public_id("rrv"),
            organisation_id=review.organisation_id,
            environment_id=review.environment_id,
            risk_review_id=review.id,
            version=version_number,
            score_version=1,
            completeness_score=breakdown.completeness,
            domain_score=breakdown.domain,
            category_score=breakdown.category,
            pricing_score=breakdown.pricing,
            claims_score=breakdown.claims,
            total_score=breakdown.total,
            severity=severity,
            confidence=confidence,
            breakdown=version_payload,
            review_sha256=hashlib.sha256(canonical_json_bytes(version_payload)).digest(),
            hard_stop=hard_stop,
        )
        session.add(version)
        session.flush([version])
        reason = None
        if hard_stop:
            reason = escalation_reason(hard_stop_classification, severity)
        elif severity in {"HIGH", "CRITICAL"}:
            reason = "CRITICAL_SEVERITY" if severity == "CRITICAL" else "HIGH_SEVERITY"
        if reason is not None:
            existing_escalation = session.scalar(
                select(RiskEscalation).where(RiskEscalation.risk_review_id == review.id)
            )
            if existing_escalation is None:
                session.add(
                    RiskEscalation(
                        id=new_uuid(),
                        public_id=new_public_id("res"),
                        organisation_id=review.organisation_id,
                        environment_id=review.environment_id,
                        risk_review_id=review.id,
                        risk_review_version_id=version.id,
                        reason=reason,
                        status="OPEN",
                    )
                )
            review.status = "ESCALATED"
        else:
            review.status = "COMPLETED"
        from relaypay.agent_runtime.events import append_business_event

        append_business_event(
            session,
            organisation_id=review.organisation_id,
            organisation_public_id=prepared.organisation_public_id,
            environment_id=review.environment_id,
            environment_public_id=prepared.environment_public_id,
            event_type=(
                "risk-review.escalated.v1"
                if review.status == "ESCALATED"
                else "risk-review.completed.v1"
            ),
            resource_type="risk_review",
            resource_id=review.public_id,
            payload={
                "reason": reason,
                "totalScore": breakdown.total,
                "severity": severity,
                "confidence": confidence,
                "hardStop": hard_stop,
            },
            now=now,
        )
    # Read the committed state only after the write transaction has closed.
    return read_review_payload(session_factory, review.public_id)


def read_review_payload(
    session_factory: sessionmaker[Session],
    review_public_id: str,
    *,
    organisation_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
) -> dict[str, object]:
    with session_factory() as session, session.begin():
        statement = select(RiskReview).where(RiskReview.public_id == review_public_id)
        if organisation_id is not None:
            statement = statement.where(RiskReview.organisation_id == organisation_id)
        if environment_id is not None:
            statement = statement.where(RiskReview.environment_id == environment_id)
        review = session.scalar(statement)
        if review is None:
            raise not_found("Risk review")
        versions = list(
            session.scalars(
                select(RiskReviewVersion)
                .where(RiskReviewVersion.risk_review_id == review.id)
                .order_by(RiskReviewVersion.version)
            ).all()
        )
        findings = list(
            session.scalars(
                select(RiskFinding)
                .where(RiskFinding.risk_review_id == review.id)
                .order_by(RiskFinding.position)
            ).all()
        )
        escalation = session.scalar(
            select(RiskEscalation).where(RiskEscalation.risk_review_id == review.id)
        )
        snapshot_row = session.get(OnboardingSnapshot, review.onboarding_snapshot_id)
        return {
            "id": review.public_id,
            "status": review.status,
            "siteRef": snapshot_row.site_ref if snapshot_row is not None else None,
            "snapshotSha256": snapshot_row.snapshot_sha256.hex()
            if snapshot_row is not None
            else None,
            "scoreVersion": review.score_version,
            "versions": [
                {
                    "id": version.public_id,
                    "version": version.version,
                    "totalScore": version.total_score,
                    "completeness": version.completeness_score,
                    "domain": version.domain_score,
                    "category": version.category_score,
                    "pricing": version.pricing_score,
                    "claims": version.claims_score,
                    "severity": version.severity,
                    "confidence": version.confidence,
                    "hardStop": version.hard_stop,
                    "reviewSha256": version.review_sha256.hex(),
                    "breakdown": version.breakdown,
                }
                for version in versions
            ],
            "findings": [
                {
                    "id": finding.public_id,
                    "type": finding.finding_type,
                    "checkKey": finding.check_key,
                    "classification": finding.classification,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "detail": finding.detail,
                    "quote": finding.quote,
                    "sourcePath": finding.source_path,
                    "snapshotSha256": finding.snapshot_sha256.hex(),
                    "hardStop": finding.hard_stop,
                }
                for finding in findings
            ],
            "escalation": None
            if escalation is None
            else {
                "id": escalation.public_id,
                "reason": escalation.reason,
                "status": escalation.status,
                "disposition": escalation.disposition,
                "dispositionNote": escalation.disposition_note,
                "dispositionedAt": escalation.dispositioned_at.isoformat()
                if escalation.dispositioned_at
                else None,
            },
        }


def list_reviews(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    limit: int = 50,
) -> list[tuple[RiskReview, str]]:
    rows = session.execute(
        select(RiskReview, OnboardingSnapshot.site_ref)
        .join(OnboardingSnapshot, OnboardingSnapshot.id == RiskReview.onboarding_snapshot_id)
        .where(
            RiskReview.organisation_id == organisation_id,
            RiskReview.environment_id == environment_id,
        )
        .order_by(RiskReview.created_at.desc(), RiskReview.id.desc())
        .limit(limit)
    ).all()
    return [(review, site_ref) for review, site_ref in rows]


def annotate_review(
    session: Session,
    *,
    review: RiskReview,
    author_user_id: uuid.UUID,
    note: str,
) -> RiskAnnotation:
    if not note.strip() or len(note) > 2000:
        raise RelayPayError(
            code="RISK_ANNOTATION_INVALID",
            message="Annotation note must be between 1 and 2000 characters",
            http_status=422,
        )
    # Content-keyed replay: resubmitting an identical note by the same
    # analyst returns the existing annotation instead of appending a
    # duplicate, so a retried POST cannot double-append.
    existing = session.scalar(
        select(RiskAnnotation).where(
            RiskAnnotation.risk_review_id == review.id,
            RiskAnnotation.author_user_id == author_user_id,
            RiskAnnotation.note == note,
        )
    )
    if existing is not None:
        return existing
    item = RiskAnnotation(
        id=new_uuid(),
        public_id=new_public_id("ran"),
        organisation_id=review.organisation_id,
        environment_id=review.environment_id,
        risk_review_id=review.id,
        author_user_id=author_user_id,
        note=note,
    )
    session.add(item)
    return item


def disposition_escalation(
    session: Session,
    *,
    escalation: RiskEscalation,
    review: RiskReview,
    disposition: str,
    note: str,
    actor_user_id: uuid.UUID,
    now: datetime,
) -> RiskEscalation:
    if escalation.status == "DISPOSITIONED":
        raise RelayPayError(
            code="RISK_ESCALATION_CLOSED",
            message="Escalation is already dispositioned",
            http_status=409,
        )
    if disposition not in {"REJECT_ONBOARDING", "REQUEST_DOCUMENTS", "CLOSE_NO_ACTION"}:
        raise ValueError("unsupported disposition")
    escalation.status = "DISPOSITIONED"
    escalation.disposition = disposition
    escalation.disposition_note = note
    escalation.dispositioned_by_user_id = actor_user_id
    escalation.dispositioned_at = now
    review.status = "DISPOSITIONED"
    return escalation
