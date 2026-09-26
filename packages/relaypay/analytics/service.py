"""Cross-workflow portfolio analytics computed entirely in PostgreSQL.

Every ratio is scoped to one organisation and environment; tenant identity
never leaves the database and never reaches Prometheus labels. Manual-time
baselines are a versioned code definition so handling-time reduction stays
reproducible across releases.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relaypay.agent_runtime.models import (
    ModelInvocation,
    WorkflowRun,
    WorkflowStep,
)
from relaypay.disputes.models import DisputeCase, DisputeDraftVersion, DisputePackageVersion
from relaypay.risk_review.models import RiskEscalation, RiskReview
from relaypay.settlement_intelligence.models import SettlementQuestion
from relaypay.subscriptions.models import RecoveryCase, ScheduledRecoveryAction, SubscriptionInvoice

ANALYTICS_VERSION = 1

# Versioned manual handling-time baselines in minutes per completed workflow kind.
MANUAL_TIME_BASELINE_VERSION = 1
MANUAL_TIME_BASELINES_MINUTES: dict[str, float] = {
    "dispute-response": 240.0,
    "subscription-recovery": 90.0,
    "settlement-intelligence": 45.0,
    "merchant-risk-review": 120.0,
}


def _workflow_kind(route: str) -> str:
    if route.startswith("QUESTION:settlement"):
        return "settlement-intelligence"
    if route.startswith("EVENT:recurring-payment"):
        return "subscription-recovery"
    if route.startswith("EVENT:dispute"):
        return "dispute-response"
    if route.startswith("RISK:"):
        return "merchant-risk-review"
    return "other"


@dataclass(frozen=True, slots=True)
class Ratio:
    numerator: int
    denominator: int

    def as_dict(self) -> dict[str, object]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": round(self.numerator / self.denominator, 6) if self.denominator else None,
        }


@dataclass(frozen=True, slots=True)
class PortfolioAnalytics:
    organisation_id: uuid.UUID
    environment_id: uuid.UUID
    analytics_version: int = ANALYTICS_VERSION
    manual_time_baseline_version: int = MANUAL_TIME_BASELINE_VERSION
    dispute_packages_completed: int = 0
    dispute_cases_total: int = 0
    evidence_retrieval_precision: dict[str, object] = field(default_factory=dict)
    recovered_invoices: int = 0
    recovered_inr_paise: int = 0
    recovery: dict[str, object] = field(default_factory=dict)
    handling_time: dict[str, object] = field(default_factory=dict)
    analyst_intervention_rate: dict[str, object] = field(default_factory=dict)
    invalid_action_rate: dict[str, object] = field(default_factory=dict)
    workflow_latency: dict[str, object] = field(default_factory=dict)
    model_latency: dict[str, object] = field(default_factory=dict)
    model_cost_usd_micros_per_completed_workflow: int = 0
    completion_rate_raw: dict[str, object] = field(default_factory=dict)
    completion_rate_approval_adjusted: dict[str, object] = field(default_factory=dict)
    risk_reviews_total: int = 0
    risk_escalations_open: int = 0
    settlement_questions_total: int = 0

    def payload(self) -> dict[str, object]:
        data = asdict(self)
        data["organisation_id"] = str(self.organisation_id)
        data["environment_id"] = str(self.environment_id)
        return data


def dispute_completion(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> Ratio:
    organisation_id, environment_id = scope
    cases = (
        session.scalar(
            select(func.count())
            .select_from(DisputeCase)
            .where(
                DisputeCase.organisation_id == organisation_id,
                DisputeCase.environment_id == environment_id,
            )
        )
        or 0
    )
    packages = (
        session.scalar(
            select(func.count(func.distinct(DisputePackageVersion.dispute_case_id)))
            .select_from(DisputePackageVersion)
            .join(DisputeCase, DisputeCase.id == DisputePackageVersion.dispute_case_id)
            .where(
                DisputeCase.organisation_id == organisation_id,
                DisputeCase.environment_id == environment_id,
            )
        )
        or 0
    )
    return Ratio(int(packages), int(cases))


def evidence_precision(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> Ratio:
    """Selected evidence fields over required fields across dispute drafts."""
    organisation_id, environment_id = scope
    rows = session.execute(
        select(
            func.jsonb_array_length(DisputeDraftVersion.selected_evidence),
            func.jsonb_array_length(DisputeDraftVersion.missing_evidence),
        )
        .join(DisputeCase, DisputeCase.id == DisputeDraftVersion.dispute_case_id)
        .where(
            DisputeCase.organisation_id == organisation_id,
            DisputeCase.environment_id == environment_id,
        )
    ).all()
    selected = 0
    required = 0
    for selected_count, missing_count in rows:
        selected += int(selected_count or 0)
        required += int(selected_count or 0) + int(missing_count or 0)
    return Ratio(selected, required)


def recovery_outcomes(
    session: Session, scope: tuple[uuid.UUID, uuid.UUID]
) -> tuple[Ratio, int, int]:
    organisation_id, environment_id = scope
    eligible = (
        session.scalar(
            select(func.count())
            .select_from(RecoveryCase)
            .where(
                RecoveryCase.organisation_id == organisation_id,
                RecoveryCase.environment_id == environment_id,
                RecoveryCase.status.in_(("OPEN", "SCHEDULED", "RECOVERED")),
            )
        )
        or 0
    )
    recovered = (
        session.scalar(
            select(func.count())
            .select_from(RecoveryCase)
            .where(
                RecoveryCase.organisation_id == organisation_id,
                RecoveryCase.environment_id == environment_id,
                RecoveryCase.status == "RECOVERED",
            )
        )
        or 0
    )
    recovered_inr = (
        session.scalar(
            select(func.coalesce(func.sum(SubscriptionInvoice.amount), 0))
            .select_from(RecoveryCase)
            .join(SubscriptionInvoice, SubscriptionInvoice.id == RecoveryCase.invoice_id)
            .where(
                RecoveryCase.organisation_id == organisation_id,
                RecoveryCase.environment_id == environment_id,
                RecoveryCase.status == "RECOVERED",
            )
        )
        or 0
    )
    return Ratio(int(recovered), int(eligible)), int(recovered), int(recovered_inr)


def handling_time(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> dict[str, object]:
    """Synthetic handling minutes versus the versioned manual baseline."""
    organisation_id, environment_id = scope
    rows = session.execute(
        select(WorkflowRun.route, WorkflowRun.created_at, WorkflowRun.completed_at).where(
            WorkflowRun.organisation_id == organisation_id,
            WorkflowRun.environment_id == environment_id,
            WorkflowRun.status == "SUCCEEDED",
            WorkflowRun.completed_at.is_not(None),
        )
    ).all()
    by_kind: dict[str, list[float]] = {}
    for route, created_at, completed_at in rows:
        kind = _workflow_kind(route)
        if kind not in MANUAL_TIME_BASELINES_MINUTES:
            continue
        minutes = (completed_at - created_at).total_seconds() / 60.0
        by_kind.setdefault(kind, []).append(minutes)
    kinds: dict[str, object] = {}
    automated_total = 0.0
    manual_total = 0.0
    for kind, samples in sorted(by_kind.items()):
        average = sum(samples) / len(samples)
        manual = MANUAL_TIME_BASELINES_MINUTES[kind]
        automated_total += average
        manual_total += manual
        kinds[kind] = {
            "samples": len(samples),
            "automatedMinutes": round(average, 4),
            "manualBaselineMinutes": manual,
            "reduction": round(max(0.0, 1 - average / manual), 6) if manual else None,
        }
    out: dict[str, object] = {"baselineVersion": MANUAL_TIME_BASELINE_VERSION, "kinds": kinds}
    out["overallReduction"] = (
        round(max(0.0, 1 - automated_total / manual_total), 6) if manual_total else None
    )
    return out


def analyst_interventions(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> Ratio:
    """Drafts beyond the first per case plus cancelled runs over completed workflows."""
    organisation_id, environment_id = scope
    completed = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.organisation_id == organisation_id,
                WorkflowRun.environment_id == environment_id,
                WorkflowRun.status == "SUCCEEDED",
            )
        )
        or 0
    )
    extra_drafts = (
        session.scalar(
            select(func.count())
            .select_from(DisputeDraftVersion)
            .join(DisputeCase, DisputeCase.id == DisputeDraftVersion.dispute_case_id)
            .where(
                DisputeCase.organisation_id == organisation_id,
                DisputeCase.environment_id == environment_id,
                DisputeDraftVersion.version > 1,
            )
        )
        or 0
    )
    cancelled = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.organisation_id == organisation_id,
                WorkflowRun.environment_id == environment_id,
                WorkflowRun.status.in_(("CANCELLED", "REQUIRES_REVIEW")),
            )
        )
        or 0
    )
    return Ratio(int(extra_drafts) + int(cancelled), int(completed))


def invalid_actions(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> Ratio:
    """Policy-blocked or suppressed actions over proposed recovery actions."""
    organisation_id, environment_id = scope
    proposed = (
        session.scalar(
            select(func.count())
            .select_from(ScheduledRecoveryAction)
            .where(
                ScheduledRecoveryAction.organisation_id == organisation_id,
                ScheduledRecoveryAction.environment_id == environment_id,
            )
        )
        or 0
    )
    blocked = (
        session.scalar(
            select(func.count())
            .select_from(ScheduledRecoveryAction)
            .where(
                ScheduledRecoveryAction.organisation_id == organisation_id,
                ScheduledRecoveryAction.environment_id == environment_id,
                ScheduledRecoveryAction.status.in_(("CANCELLED", "SUPPRESSED")),
            )
        )
        or 0
    )
    return Ratio(int(blocked), int(proposed))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def workflow_latency(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> dict[str, object]:
    organisation_id, environment_id = scope
    rows = session.execute(
        select(WorkflowRun.created_at, WorkflowRun.completed_at).where(
            WorkflowRun.organisation_id == organisation_id,
            WorkflowRun.environment_id == environment_id,
            WorkflowRun.status == "SUCCEEDED",
            WorkflowRun.completed_at.is_not(None),
        )
    ).all()
    durations = [
        # completed_at may be stamped by the application clock while created_at
        # comes from the database clock; clamp host/DB clock skew at zero.
        max(0.0, (completed_at - created_at).total_seconds() * 1000.0)
        for created_at, completed_at in rows
    ]
    return {
        "p50Ms": _percentile(durations, 0.50),
        "p95Ms": _percentile(durations, 0.95),
        "samples": len(durations),
    }


def model_latency_and_cost(
    session: Session, scope: tuple[uuid.UUID, uuid.UUID]
) -> tuple[dict[str, object], int]:
    organisation_id, environment_id = scope
    latencies = list(
        session.scalars(
            select(ModelInvocation.latency_ms).where(
                ModelInvocation.organisation_id == organisation_id,
                ModelInvocation.environment_id == environment_id,
            )
        ).all()
    )
    cost = (
        session.scalar(
            select(func.coalesce(func.sum(ModelInvocation.cost_usd_micros), 0)).where(
                ModelInvocation.organisation_id == organisation_id,
                ModelInvocation.environment_id == environment_id,
            )
        )
        or 0
    )
    samples = [float(value) for value in latencies]
    payload: dict[str, object] = {
        "p50Ms": _percentile(samples, 0.50),
        "p95Ms": _percentile(samples, 0.95),
        "samples": len(samples),
    }
    return payload, int(cost)


def completion_rates(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> tuple[Ratio, Ratio]:
    organisation_id, environment_id = scope
    total = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.organisation_id == organisation_id,
                WorkflowRun.environment_id == environment_id,
            )
        )
        or 0
    )
    succeeded = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.organisation_id == organisation_id,
                WorkflowRun.environment_id == environment_id,
                WorkflowRun.status == "SUCCEEDED",
            )
        )
        or 0
    )
    waiting = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.organisation_id == organisation_id,
                WorkflowRun.environment_id == environment_id,
                WorkflowRun.status == "WAITING_FOR_APPROVAL",
            )
        )
        or 0
    )
    raw = Ratio(int(succeeded), int(total))
    adjusted = Ratio(int(succeeded), int(total) - int(waiting))
    return raw, adjusted


def step_counts(session: Session, scope: tuple[uuid.UUID, uuid.UUID]) -> tuple[int, int]:
    organisation_id, environment_id = scope
    dead = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowStep)
            .where(
                WorkflowStep.organisation_id == organisation_id,
                WorkflowStep.environment_id == environment_id,
                WorkflowStep.status == "DEAD_LETTER",
            )
        )
        or 0
    )
    total = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowStep)
            .where(
                WorkflowStep.organisation_id == organisation_id,
                WorkflowStep.environment_id == environment_id,
            )
        )
        or 0
    )
    return int(dead), int(total)


def portfolio_analytics(
    session: Session, *, organisation_id: uuid.UUID, environment_id: uuid.UUID
) -> PortfolioAnalytics:
    scope = (organisation_id, environment_id)
    packages = dispute_completion(session, scope)
    precision = evidence_precision(session, scope)
    recovery_ratio, recovered_invoices, recovered_inr = recovery_outcomes(session, scope)
    raw, adjusted = completion_rates(session, scope)
    model_latency, model_cost = model_latency_and_cost(session, scope)
    completed_for_cost = (
        session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.organisation_id == organisation_id,
                WorkflowRun.environment_id == environment_id,
                WorkflowRun.status == "SUCCEEDED",
            )
        )
        or 0
    )
    risk_reviews = (
        session.scalar(
            select(func.count())
            .select_from(RiskReview)
            .where(
                RiskReview.organisation_id == organisation_id,
                RiskReview.environment_id == environment_id,
            )
        )
        or 0
    )
    open_escalations = (
        session.scalar(
            select(func.count())
            .select_from(RiskEscalation)
            .join(RiskReview, RiskReview.id == RiskEscalation.risk_review_id)
            .where(
                RiskReview.organisation_id == organisation_id,
                RiskReview.environment_id == environment_id,
                RiskEscalation.status == "OPEN",
            )
        )
        or 0
    )
    questions = (
        session.scalar(
            select(func.count())
            .select_from(SettlementQuestion)
            .where(
                SettlementQuestion.organisation_id == organisation_id,
                SettlementQuestion.environment_id == environment_id,
            )
        )
        or 0
    )
    return PortfolioAnalytics(
        organisation_id=organisation_id,
        environment_id=environment_id,
        dispute_packages_completed=packages.numerator,
        dispute_cases_total=packages.denominator,
        evidence_retrieval_precision=precision.as_dict(),
        recovered_invoices=recovered_invoices,
        recovered_inr_paise=recovered_inr,
        recovery={
            "rate": recovery_ratio.as_dict(),
            "recoveredInrPaise": recovered_inr,
        },
        handling_time=handling_time(session, scope),
        analyst_intervention_rate=analyst_interventions(session, scope).as_dict(),
        invalid_action_rate=invalid_actions(session, scope).as_dict(),
        workflow_latency=workflow_latency(session, scope),
        model_latency=model_latency,
        model_cost_usd_micros_per_completed_workflow=(
            model_cost // completed_for_cost if completed_for_cost else 0
        ),
        completion_rate_raw=raw.as_dict(),
        completion_rate_approval_adjusted=adjusted.as_dict(),
        risk_reviews_total=int(risk_reviews),
        risk_escalations_open=int(open_escalations),
        settlement_questions_total=int(questions),
    )


def refresh_portfolio_metrics(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    metrics: object,
) -> None:
    """Populate the low-cardinality portfolio gauges; no tenant labels are emitted."""
    from relaypay.observability.metrics import OperationsMetrics

    assert isinstance(metrics, OperationsMetrics)

    def as_rate(values: dict[str, object]) -> float:
        value = values.get("value")
        return float(value) if isinstance(value, int | float) else 0.0

    analytics = portfolio_analytics(
        session, organisation_id=organisation_id, environment_id=environment_id
    )
    metrics.portfolio_completion.labels("raw").set(as_rate(analytics.completion_rate_raw))
    metrics.portfolio_completion.labels("approval_adjusted").set(
        as_rate(analytics.completion_rate_approval_adjusted)
    )
    recovery_value = analytics.recovery.get("rate")
    recovery_rate = as_rate(recovery_value) if isinstance(recovery_value, dict) else 0.0
    metrics.portfolio_recovery_rate.set(recovery_rate)
    handling = analytics.handling_time.get("overallReduction")
    metrics.portfolio_handling_reduction.set(
        float(handling) if isinstance(handling, int | float) else 0.0
    )
    metrics.portfolio_model_cost.set(float(analytics.model_cost_usd_micros_per_completed_workflow))
    metrics.portfolio_analyst_interventions.set(as_rate(analytics.analyst_intervention_rate))
    metrics.portfolio_invalid_actions.set(as_rate(analytics.invalid_action_rate))
