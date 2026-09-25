"""Fixed, tenant-scoped deterministic queries behind every settlement answer.

These functions are the only source of numbers, calculation steps, citations,
and traces for settlement questions. Models never write SQL and never compute
financial values; they may only describe the results produced here.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relaypay.idempotency import canonical_json_bytes
from relaypay.merchant_balances.models import BalanceTransaction, SettlementItem, SettlementRun
from relaypay.payments.models import Capture, Refund
from relaypay.settlement_intelligence.models import (
    SettlementForecast,
    SettlementForecastItem,
    SettlementPolicy,
)
from relaypay.settlement_intelligence.windows import (
    PolicyWindow,
    arrival_date,
    business_date_of,
    cutoff_at,
    local_day_bounds,
    next_business_date,
    resolve_zone,
)

MAX_CITED_RECORDS = 100
RECORD_ID_PREFIXES = ("cap", "ref", "stl", "sfc", "sfi", "spv", "btx", "mac", "sqn")
FOREVER_START = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class QueryScope:
    organisation_id: uuid.UUID
    environment_id: uuid.UUID
    merchant_account_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Citation:
    record_type: str
    record_id: str
    field_paths: tuple[str, ...]
    snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class DeterministicResult:
    intent: str
    numbers: dict[str, int]
    dates: dict[str, str]
    calculation_steps: tuple[str, ...]
    citations: tuple[Citation, ...]
    trace: tuple[str, ...]
    required_citation_ids: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "numbers": dict(self.numbers),
            "dates": dict(self.dates),
            "calculationSteps": list(self.calculation_steps),
            "citations": [
                {
                    "recordType": citation.record_type,
                    "recordId": citation.record_id,
                    "fieldPaths": list(citation.field_paths),
                    "snapshotSha256": citation.snapshot_sha256,
                }
                for citation in self.citations
            ],
            "trace": list(self.trace),
        }


def record_digest(record_type: str, record_id: str, fields: dict[str, object]) -> str:
    """Deterministic digest over exactly the cited field values."""
    return (
        hashlib.sha256(
            canonical_json_bytes({"recordType": record_type, "recordId": record_id, **fields})
        )
        .digest()
        .hex()
    )


def _policy_citation(policy: SettlementPolicy, window: PolicyWindow) -> Citation:
    return Citation(
        record_type="settlement_policy",
        record_id=policy.public_id,
        field_paths=(
            "timezoneName",
            "cutoffHour",
            "cutoffMinute",
            "settlementDelayDays",
            "weekendHandling",
        ),
        snapshot_sha256=policy.policy_sha256.hex(),
    )


def _capture_citation(capture: Capture) -> Citation:
    return Citation(
        record_type="capture",
        record_id=capture.public_id,
        field_paths=("amount", "capturedAt", "status"),
        snapshot_sha256=record_digest(
            "capture",
            capture.public_id,
            {
                "amount": capture.amount,
                "capturedAt": (capture.captured_at or FOREVER_START).isoformat(),
            },
        ),
    )


def _refund_citation(refund: Refund) -> Citation:
    return Citation(
        record_type="refund",
        record_id=refund.public_id,
        field_paths=("amount", "refundedAt", "status"),
        snapshot_sha256=record_digest(
            "refund",
            refund.public_id,
            {
                "amount": refund.amount,
                "refundedAt": (refund.refunded_at or FOREVER_START).isoformat(),
            },
        ),
    )


def _unsettled_captures(
    session: Session,
    scope: QueryScope,
    *,
    max_captured_at: datetime,
    min_captured_at: datetime | None = None,
) -> list[Capture]:
    statement = (
        select(Capture)
        .join(SettlementItem, SettlementItem.capture_id == Capture.id, isouter=True)
        .where(
            Capture.organisation_id == scope.organisation_id,
            Capture.environment_id == scope.environment_id,
            Capture.status == "SUCCEEDED",
            Capture.captured_at <= max_captured_at,
            SettlementItem.id.is_(None),
        )
        .order_by(Capture.captured_at, Capture.id)
    )
    if min_captured_at is not None:
        statement = statement.where(Capture.captured_at > min_captured_at)
    return list(session.scalars(statement).all())


def _receivable_balance(
    session: Session, scope: QueryScope, *, as_of: datetime, since: datetime | None = None
) -> int:
    statement = select(func.coalesce(func.sum(BalanceTransaction.receivable_delta), 0)).where(
        BalanceTransaction.organisation_id == scope.organisation_id,
        BalanceTransaction.environment_id == scope.environment_id,
        BalanceTransaction.merchant_account_id == scope.merchant_account_id,
        BalanceTransaction.created_at <= as_of,
    )
    if since is not None:
        statement = statement.where(BalanceTransaction.created_at > since)
    return int(session.scalar(statement) or 0)


def _succeeded_refunds(
    session: Session, scope: QueryScope, *, start: datetime, end: datetime
) -> list[Refund]:
    return list(
        session.scalars(
            select(Refund)
            .where(
                Refund.organisation_id == scope.organisation_id,
                Refund.environment_id == scope.environment_id,
                Refund.status == "SUCCEEDED",
                Refund.refunded_at > start,
                Refund.refunded_at <= end,
            )
            .order_by(Refund.refunded_at, Refund.id)
        ).all()
    )


def _policy_numbers(window: PolicyWindow) -> dict[str, int]:
    return {
        "cutoffHour": window.cutoff_hour,
        "cutoffMinute": window.cutoff_minute,
        "settlementDelayDays": window.settlement_delay_days,
    }


def why_lower_settlement(
    session: Session,
    *,
    scope: QueryScope,
    policy: SettlementPolicy,
    window: PolicyWindow,
    question_date: date,
    as_of: datetime,
) -> DeterministicResult:
    """Compare the settlement that arrived with the latest pre-cutoff forecast."""
    numbers: dict[str, int] = _policy_numbers(window)
    steps: list[str] = []
    trace: list[str] = [
        f"query:latest_forecast filters=(scope, business_date={question_date.isoformat()}) "
        "order=(sequence desc)"
    ]
    citations: list[Citation] = [_policy_citation(policy, window)]
    required: list[str] = [policy.public_id]

    forecast = session.scalar(
        select(SettlementForecast)
        .where(
            SettlementForecast.organisation_id == scope.organisation_id,
            SettlementForecast.environment_id == scope.environment_id,
            SettlementForecast.merchant_account_id == scope.merchant_account_id,
            SettlementForecast.business_date == question_date,
        )
        .order_by(SettlementForecast.sequence.desc())
        .limit(1)
    )
    numbers["forecastAvailable"] = 1 if forecast is not None else 0
    if forecast is not None:
        numbers["forecastCaptureTotal"] = forecast.capture_total
        numbers["forecastRefundTotal"] = forecast.refund_total
        numbers["forecastReceivableOffsetTotal"] = forecast.receivable_offset_total
        numbers["forecastExpectedSettlement"] = forecast.expected_settlement_amount
        citations.append(
            Citation(
                record_type="settlement_forecast",
                record_id=forecast.public_id,
                field_paths=(
                    "captureTotal",
                    "refundTotal",
                    "receivableOffsetTotal",
                    "expectedSettlementAmount",
                    "businessDate",
                ),
                snapshot_sha256=forecast.snapshot_sha256.hex(),
            )
        )
        required.append(forecast.public_id)
        steps.append(
            f"Latest pre-cutoff forecast {forecast.public_id} (sequence {forecast.sequence}) "
            f"expected settlement {forecast.expected_settlement_amount} paise from "
            f"{forecast.capture_count} captures."
        )
    else:
        steps.append(
            f"No immutable pre-cutoff forecast exists for {question_date.isoformat()}; "
            f"the forecast baseline is zero."
        )

    day_start, day_end = local_day_bounds(window, question_date)
    trace.append(
        "query:settled_today join=(settlement_items, settlement_runs) "
        "filters=(scope, completed_at within local day)"
    )
    settled_rows = list(
        session.execute(
            select(SettlementRun, func.coalesce(func.sum(SettlementItem.amount), 0))
            .join(SettlementItem, SettlementItem.settlement_run_id == SettlementRun.id)
            .where(
                SettlementRun.organisation_id == scope.organisation_id,
                SettlementRun.environment_id == scope.environment_id,
                SettlementRun.merchant_account_id == scope.merchant_account_id,
                SettlementRun.status == "COMPLETED",
                SettlementRun.completed_at >= day_start,
                SettlementRun.completed_at < day_end,
            )
            .group_by(SettlementRun.id)
            .order_by(SettlementRun.created_at, SettlementRun.id)
        ).all()
    )
    actual_settlement = sum(int(amount) for _, amount in settled_rows)
    numbers["actualSettlement"] = actual_settlement
    numbers["settledRunCount"] = len(settled_rows)
    for run, amount in settled_rows[:MAX_CITED_RECORDS]:
        citations.append(
            Citation(
                record_type="settlement_run",
                record_id=run.public_id,
                field_paths=("settledAmount", "status", "completedAt"),
                snapshot_sha256=(run.response_sha256 or b"").hex()
                or record_digest("settlement_run", run.public_id, {"settledAmount": int(amount)}),
            )
        )
        required.append(run.public_id)
    steps.append(f"Completed settlement runs in the local day settled {actual_settlement} paise.")

    if forecast is not None:
        forecast_captures = list(
            session.scalars(
                select(Capture)
                .join(
                    SettlementForecastItem,
                    SettlementForecastItem.capture_id == Capture.id,
                )
                .where(
                    SettlementForecastItem.organisation_id == scope.organisation_id,
                    SettlementForecastItem.environment_id == scope.environment_id,
                    SettlementForecastItem.forecast_id == forecast.id,
                    SettlementForecastItem.item_type == "CAPTURE",
                )
            ).all()
        )
        forecast_capture_ids = {capture.id for capture in forecast_captures}
        cutoff = cutoff_at(window, question_date)
        late_refunds = [
            refund
            for refund in _succeeded_refunds(session, scope, start=forecast.created_at, end=as_of)
            if refund.capture_id in forecast_capture_ids
        ]
        late_refund_total = sum(refund.amount for refund in late_refunds)
        numbers["lateRefundTotal"] = late_refund_total
        numbers["lateRefundCount"] = len(late_refunds)
        for refund in late_refunds[: MAX_CITED_RECORDS - len(settled_rows)]:
            citations.append(_refund_citation(refund))
        steps.append(
            f"Refunds succeeded after the forecast was recorded reduced the outcome by "
            f"{late_refund_total} paise across {len(late_refunds)} refunds."
        )
        late_captures = [
            capture
            for capture in _unsettled_captures(
                session,
                scope,
                max_captured_at=cutoff,
                min_captured_at=forecast.created_at,
            )
            if capture.id not in forecast_capture_ids
        ]
        late_capture_total = sum(capture.amount for capture in late_captures)
        numbers["lateCaptureTotal"] = late_capture_total
        numbers["lateCaptureCount"] = len(late_captures)
        steps.append(
            f"Captures recorded after the forecast but before the cutoff added "
            f"{late_capture_total} paise across {len(late_captures)} captures."
        )
        receivable_change = _receivable_balance(
            session, scope, as_of=as_of, since=forecast.created_at
        )
        numbers["receivableOffsetDelta"] = receivable_change
        steps.append(
            f"Receivable positions created after the forecast divert {receivable_change} "
            f"paise of captured value from this payout."
        )
        numbers["settlementDelta"] = actual_settlement - forecast.expected_settlement_amount
        steps.append(
            f"Settlement delta: actual {actual_settlement} minus forecast "
            f"{forecast.expected_settlement_amount} equals "
            f"{numbers['settlementDelta']} paise."
        )
    else:
        numbers["settlementDelta"] = actual_settlement

    dates = {
        "questionDate": question_date.isoformat(),
        "asOf": as_of.isoformat(),
    }
    return DeterministicResult(
        intent="SETTLEMENT_LOWER",
        numbers=numbers,
        dates=dates,
        calculation_steps=tuple(steps),
        citations=tuple(citations),
        trace=tuple(trace),
        required_citation_ids=tuple(dict.fromkeys(required)),
    )


def unsettled_payments(
    session: Session,
    *,
    scope: QueryScope,
    policy: SettlementPolicy,
    window: PolicyWindow,
    question_date: date,
    as_of: datetime,
) -> DeterministicResult:
    numbers = _policy_numbers(window)
    captures = _unsettled_captures(session, scope, max_captured_at=as_of)
    numbers["unsettledCount"] = len(captures)
    numbers["unsettledTotal"] = sum(capture.amount for capture in captures)
    steps = [
        "Selected succeeded captures in scope with no settlement item.",
        f"Summed {numbers['unsettledTotal']} paise across {len(captures)} unsettled captures.",
    ]
    citations: list[Citation] = [_policy_citation(policy, window)]
    required = [policy.public_id]
    arrivals: set[str] = set()
    for capture in captures[:MAX_CITED_RECORDS]:
        citations.append(_capture_citation(capture))
        required.append(capture.public_id)
        if capture.captured_at is not None:
            arrivals.add(
                arrival_date(window, business_date_of(window, capture.captured_at)).isoformat()
            )
    if captures:
        steps.append(
            f"Applied policy {policy.public_id} to each capture: expected arrival is the "
            f"business date plus T+{window.settlement_delay_days} with weekends "
            f"{window.weekend_handling}."
        )
    dates = {
        "questionDate": question_date.isoformat(),
        "asOf": as_of.isoformat(),
    }
    for value in sorted(arrivals):
        dates.setdefault("expectedArrival", value)
    return DeterministicResult(
        intent="UNSETTLED_PAYMENTS",
        numbers=numbers,
        dates=dates,
        calculation_steps=tuple(steps),
        citations=tuple(citations),
        trace=(
            "query:unsettled_captures join=(settlement_items is null) filters=(scope, "
            "status=SUCCEEDED, captured_at<=as_of)",
        ),
        required_citation_ids=tuple(dict.fromkeys(required)),
    )


def refund_cash_impact(
    session: Session,
    *,
    scope: QueryScope,
    policy: SettlementPolicy,
    window: PolicyWindow,
    question_date: date,
    as_of: datetime,
) -> DeterministicResult:
    numbers = _policy_numbers(window)
    # The cash-impact window is the local calendar day containing the answer
    # moment, clamped to that moment; it never reaches into the future.
    zone = resolve_zone(window)
    day_start = datetime.combine(as_of.astimezone(zone).date(), time.min, tzinfo=zone)
    window_end = as_of
    refunds = _succeeded_refunds(session, scope, start=day_start, end=window_end)
    numbers["refundCount"] = len(refunds)
    numbers["refundTotal"] = sum(refund.amount for refund in refunds)
    reduces_pending = 0
    draws_available = 0
    creates_receivable = 0
    citations: list[Citation] = [_policy_citation(policy, window)]
    required = [policy.public_id]
    for refund in refunds[:MAX_CITED_RECORDS]:
        citations.append(_refund_citation(refund))
        required.append(refund.public_id)
        deltas = list(
            session.scalars(
                select(BalanceTransaction).where(
                    BalanceTransaction.organisation_id == scope.organisation_id,
                    BalanceTransaction.environment_id == scope.environment_id,
                    BalanceTransaction.merchant_account_id == scope.merchant_account_id,
                    BalanceTransaction.journal_id == refund.journal_id,
                    BalanceTransaction.transaction_type == "REFUND",
                )
            ).all()
        )
        for delta in deltas:
            reduces_pending += abs(delta.pending_delta)
            draws_available += abs(delta.available_delta)
            creates_receivable += delta.receivable_delta
            citations.append(
                Citation(
                    record_type="balance_transaction",
                    record_id=delta.public_id,
                    field_paths=("pendingDelta", "availableDelta", "receivableDelta"),
                    snapshot_sha256=record_digest(
                        "balance_transaction",
                        delta.public_id,
                        {
                            "pendingDelta": delta.pending_delta,
                            "availableDelta": delta.available_delta,
                            "receivableDelta": delta.receivable_delta,
                        },
                    ),
                )
            )
    numbers["reducesPendingPayable"] = reduces_pending
    numbers["drawsAvailablePayable"] = draws_available
    numbers["createsReceivable"] = creates_receivable
    steps = [
        f"Selected refunds that succeeded between local midnight {day_start.isoformat()} "
        f"and {window_end.isoformat()}.",
        "Joined each refund journal to its immutable balance transaction projection.",
        f"Cash impact: {reduces_pending} paise reduced pending payable, {draws_available} "
        f"paise drew from available payable, and {creates_receivable} paise created "
        f"merchant receivable.",
    ]
    return DeterministicResult(
        intent="REFUND_IMPACT",
        numbers=numbers,
        dates={
            "questionDate": question_date.isoformat(),
            "windowStart": day_start.isoformat(),
            "windowEnd": window_end.isoformat(),
        },
        calculation_steps=tuple(steps),
        citations=tuple(citations),
        trace=(
            "query:refund_balance_impact join=(refunds, balance_transactions by journal) "
            "filters=(scope, status=SUCCEEDED, refunded_at in local day)",
        ),
        required_citation_ids=tuple(dict.fromkeys(required)),
    )


def expected_arrival_tomorrow(
    session: Session,
    *,
    scope: QueryScope,
    policy: SettlementPolicy,
    window: PolicyWindow,
    question_date: date,
    as_of: datetime,
) -> DeterministicResult:
    # "Tomorrow" is the next settlement business date after the operator's
    # local calendar day, independent of the cutoff roll-forward.
    local_today = as_of.astimezone(resolve_zone(window)).date()
    business_date = next_business_date(window, local_today)
    cutoff = cutoff_at(window, business_date)
    arrival = arrival_date(window, business_date)
    numbers = _policy_numbers(window)
    captures = _unsettled_captures(session, scope, max_captured_at=cutoff)
    capture_total = sum(capture.amount for capture in captures)
    capture_ids = {capture.id for capture in captures}
    refunds = [
        refund
        for refund in _succeeded_refunds(session, scope, start=FOREVER_START, end=cutoff)
        if refund.capture_id in capture_ids
    ]
    refund_total = sum(refund.amount for refund in refunds)
    receivable = _receivable_balance(session, scope, as_of=as_of)
    offset = min(receivable, max(0, capture_total - refund_total))
    expected = capture_total - refund_total - offset
    numbers["expectedCaptureTotal"] = capture_total
    numbers["expectedCaptureCount"] = len(captures)
    numbers["expectedRefundTotal"] = refund_total
    numbers["openReceivable"] = receivable
    numbers["receivableOffsetTotal"] = offset
    numbers["expectedSettlement"] = expected
    steps = [
        f"Tomorrow is business date {business_date.isoformat()} with cutoff {cutoff.isoformat()}.",
        f"Eligible unsettled captures total {capture_total} paise across {len(captures)} captures.",
        f"Succeeded refunds against those captures total {refund_total} paise.",
        f"Open receivable of {receivable} paise offsets the payout by {offset} paise.",
        f"Expected settlement: {capture_total} - {refund_total} - {offset} = "
        f"{expected} paise arriving {arrival.isoformat()}.",
    ]
    citations: list[Citation] = [_policy_citation(policy, window)]
    required = [policy.public_id]
    for capture in captures[:MAX_CITED_RECORDS]:
        citations.append(_capture_citation(capture))
        required.append(capture.public_id)
    for refund in refunds[: MAX_CITED_RECORDS - len(captures)]:
        citations.append(_refund_citation(refund))
    return DeterministicResult(
        intent="ARRIVAL_TOMORROW",
        numbers=numbers,
        dates={
            "questionDate": question_date.isoformat(),
            "businessDate": business_date.isoformat(),
            "cutoffAt": cutoff.isoformat(),
            "expectedArrival": arrival.isoformat(),
        },
        calculation_steps=tuple(steps),
        citations=tuple(citations),
        trace=(
            "query:unsettled_captures join=(settlement_items is null) filters=(scope, "
            "captured_at<=next_cutoff)",
        ),
        required_citation_ids=tuple(dict.fromkeys(required)),
    )


INTENT_QUERIES = {
    "SETTLEMENT_LOWER": why_lower_settlement,
    "UNSETTLED_PAYMENTS": unsettled_payments,
    "REFUND_IMPACT": refund_cash_impact,
    "ARRIVAL_TOMORROW": expected_arrival_tomorrow,
}
