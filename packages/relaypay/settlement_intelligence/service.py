"""Settlement intelligence policies, forecasts, and question answering.

The model role is bounded to intent classification and narration; every number
comes from :mod:`relaypay.settlement_intelligence.queries`. Provider calls are
network I/O and always happen outside database transactions.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.agent_runtime.contracts import ModelRequest, ModelResult, StructuredModelProvider
from relaypay.agent_runtime.events import append_business_event
from relaypay.agent_runtime.models import (
    ModelInvocation,
    PricingVersion,
    PromptVersion,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)
from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_public_id, new_uuid
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.settlement_intelligence.classification import (
    CLASSIFICATION_TEMPLATE,
    QuestionClassification,
    classification_prompt,
    resolve_question_date,
)
from relaypay.settlement_intelligence.models import (
    SettlementForecast,
    SettlementForecastItem,
    SettlementPolicy,
    SettlementQuestion,
)
from relaypay.settlement_intelligence.narrative import (
    NARRATIVE_TEMPLATE,
    SettlementNarrative,
    narrative_prompt,
    validate_narrative,
)
from relaypay.settlement_intelligence.queries import (
    INTENT_QUERIES,
    DeterministicResult,
    QueryScope,
    _receivable_balance,
    _succeeded_refunds,
    _unsettled_captures,
)
from relaypay.settlement_intelligence.windows import (
    PolicyWindow,
    arrival_date,
    business_date_of,
    cutoff_at,
    next_business_date,
    validate_window,
)

QUESTION_MODEL_ID = "settlement-intelligence-v1"
CLASSIFICATION_STEP_KEY = "classify-intent"
NARRATIVE_STEP_KEY = "narrate-answer"
QUESTION_DEFINITION_STEPS: list[dict[str, object]] = [
    {"key": CLASSIFICATION_STEP_KEY, "kind": "MODEL"},
    {"key": NARRATIVE_STEP_KEY, "kind": "MODEL"},
]
QUESTION_TOKEN_BUDGET = 8_000
QUESTION_COST_BUDGET_USD_MICROS = 80_000
MAX_QUESTION_LENGTH = 2_000


@dataclass(frozen=True, slots=True)
class PreparedQuestion:
    question_id: uuid.UUID
    question_public_id: str
    run_public_id: str
    merchant_account_id: uuid.UUID
    organisation_public_id: str
    environment_public_id: str
    replayed: bool


def policy_window(policy: SettlementPolicy) -> PolicyWindow:
    return PolicyWindow(
        timezone_name=policy.timezone_name,
        cutoff_hour=policy.cutoff_hour,
        cutoff_minute=policy.cutoff_minute,
        settlement_delay_days=policy.settlement_delay_days,
        weekend_handling=policy.weekend_handling,
    )


def active_policy(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
) -> SettlementPolicy | None:
    return session.scalar(
        select(SettlementPolicy).where(
            SettlementPolicy.organisation_id == organisation_id,
            SettlementPolicy.environment_id == environment_id,
            SettlementPolicy.merchant_account_id == merchant_account_id,
            SettlementPolicy.status == "ACTIVE",
        )
    )


def ensure_default_policy(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
) -> SettlementPolicy:
    existing = active_policy(
        session,
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
    )
    if existing is not None:
        return existing
    return create_policy(
        session,
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
        window=PolicyWindow.default(),
    )


def create_policy(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
    window: PolicyWindow,
) -> SettlementPolicy:
    validate_window(window)
    account = session.scalar(
        select(MerchantAccount).where(
            MerchantAccount.organisation_id == organisation_id,
            MerchantAccount.environment_id == environment_id,
            MerchantAccount.id == merchant_account_id,
        )
    )
    if account is None:
        raise not_found("Merchant account")
    latest_version = session.scalar(
        select(SettlementPolicy.version)
        .where(
            SettlementPolicy.organisation_id == organisation_id,
            SettlementPolicy.environment_id == environment_id,
            SettlementPolicy.merchant_account_id == merchant_account_id,
        )
        .order_by(SettlementPolicy.version.desc())
        .limit(1)
    )
    for previous in session.scalars(
        select(SettlementPolicy).where(
            SettlementPolicy.organisation_id == organisation_id,
            SettlementPolicy.environment_id == environment_id,
            SettlementPolicy.merchant_account_id == merchant_account_id,
            SettlementPolicy.status == "ACTIVE",
        )
    ).all():
        previous.status = "RETIRED"
    digest = hashlib.sha256(canonical_json_bytes(window.policy_value())).digest()
    item = SettlementPolicy(
        id=new_uuid(),
        public_id=new_public_id("spv"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
        version=(latest_version or 0) + 1,
        timezone_name=window.timezone_name,
        cutoff_hour=window.cutoff_hour,
        cutoff_minute=window.cutoff_minute,
        settlement_delay_days=window.settlement_delay_days,
        weekend_handling=window.weekend_handling,
        policy_sha256=digest,
        status="ACTIVE",
    )
    session.add(item)
    session.flush([item])
    return item


def record_pre_cutoff_forecast(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
    policy: SettlementPolicy,
    now: datetime,
) -> SettlementForecast:
    """Write one immutable pre-cutoff forecast snapshot with deterministic items."""
    window = policy_window(policy)
    business_date = business_date_of(window, now)
    cutoff = cutoff_at(window, business_date)
    if now > cutoff:
        business_date = next_business_date(window, business_date)
        cutoff = cutoff_at(window, business_date)
    scope = QueryScope(organisation_id, environment_id, merchant_account_id)
    captures = _unsettled_captures(session, scope, max_captured_at=cutoff)
    capture_total = sum(capture.amount for capture in captures)
    capture_ids = {capture.id for capture in captures}
    refunds = [
        refund
        for refund in _succeeded_refunds(
            session,
            scope,
            start=datetime(2000, 1, 1, tzinfo=UTC),
            end=cutoff,
        )
        if refund.capture_id in capture_ids
    ]
    refund_total = sum(refund.amount for refund in refunds)
    receivable = _receivable_balance(session, scope, as_of=now)
    offset = min(receivable, max(0, capture_total - refund_total))
    expected = capture_total - refund_total - offset
    snapshot: dict[str, object] = {
        "businessDate": business_date.isoformat(),
        "cutoffAt": cutoff.isoformat(),
        "expectedArrivalDate": arrival_date(window, business_date).isoformat(),
        "policyVersion": policy.version,
        "captureTotal": capture_total,
        "refundTotal": refund_total,
        "receivableOffsetTotal": offset,
        "expectedSettlementAmount": expected,
        "captureCount": len(captures),
        "refundCount": len(refunds),
        "recordedAt": now.isoformat(),
    }
    snapshot_digest = hashlib.sha256(canonical_json_bytes(snapshot)).digest()
    sequence = (
        session.scalar(
            select(func.count(SettlementForecast.id)).where(
                SettlementForecast.organisation_id == organisation_id,
                SettlementForecast.environment_id == environment_id,
                SettlementForecast.merchant_account_id == merchant_account_id,
                SettlementForecast.business_date == business_date,
            )
        )
        or 0
    ) + 1
    item = SettlementForecast(
        id=new_uuid(),
        public_id=new_public_id("sfc"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
        policy_id=policy.id,
        created_at=now,
        business_date=business_date,
        sequence=sequence,
        cutoff_at=cutoff,
        expected_arrival_date=arrival_date(window, business_date),
        capture_total=capture_total,
        refund_total=refund_total,
        receivable_offset_total=offset,
        expected_settlement_amount=expected,
        capture_count=len(captures),
        refund_count=len(refunds),
        currency="INR",
        snapshot=snapshot,
        snapshot_sha256=snapshot_digest,
    )
    session.add(item)
    session.flush([item])
    for capture in captures:
        session.add(
            SettlementForecastItem(
                id=new_uuid(),
                public_id=new_public_id("sfi"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                forecast_id=item.id,
                item_type="CAPTURE",
                capture_id=capture.id,
                amount=capture.amount,
                currency="INR",
            )
        )
    for refund in refunds:
        session.add(
            SettlementForecastItem(
                id=new_uuid(),
                public_id=new_public_id("sfi"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                forecast_id=item.id,
                item_type="REFUND",
                capture_id=refund.capture_id,
                refund_id=refund.id,
                amount=-refund.amount,
                currency="INR",
            )
        )
    if offset > 0:
        session.add(
            SettlementForecastItem(
                id=new_uuid(),
                public_id=new_public_id("sfi"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                forecast_id=item.id,
                item_type="RECEIVABLE_OFFSET",
                amount=-offset,
                currency="INR",
            )
        )
    return item


def ensure_daily_forecast(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
    now: datetime,
) -> SettlementForecast | None:
    """Beat-task-safe single forecast per business date; never rewrites history."""
    policy = active_policy(
        session,
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
    )
    if policy is None:
        return None
    window = policy_window(policy)
    business_date = business_date_of(window, now)
    if now > cutoff_at(window, business_date):
        business_date = next_business_date(window, business_date)
    existing = session.scalar(
        select(SettlementForecast.id).where(
            SettlementForecast.organisation_id == organisation_id,
            SettlementForecast.environment_id == environment_id,
            SettlementForecast.merchant_account_id == merchant_account_id,
            SettlementForecast.business_date == business_date,
        )
    )
    if existing is not None:
        return None
    return record_pre_cutoff_forecast(
        session,
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
        policy=policy,
        now=now,
    )


def _ensure_question_definition(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
) -> WorkflowDefinition:
    definition = session.scalar(
        select(WorkflowDefinition).where(
            WorkflowDefinition.organisation_id == organisation_id,
            WorkflowDefinition.environment_id == environment_id,
            WorkflowDefinition.name == "settlement-intelligence",
            WorkflowDefinition.status == "ACTIVE",
        )
    )
    if definition is not None:
        return definition
    value = {"steps": QUESTION_DEFINITION_STEPS}
    definition = WorkflowDefinition(
        id=new_uuid(),
        public_id=new_public_id("wdf"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        name="settlement-intelligence",
        version=1,
        definition_sha256=hashlib.sha256(canonical_json_bytes(value)).digest(),
        definition=value,
        status="ACTIVE",
    )
    session.add(definition)
    session.flush([definition])
    return definition


def _ensure_prompt_version(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    name: str,
    template: str,
) -> PromptVersion:
    digest = hashlib.sha256(template.encode()).digest()
    existing = session.scalar(
        select(PromptVersion).where(
            PromptVersion.name == name,
            PromptVersion.version == 1,
            PromptVersion.prompt_sha256 == digest,
        )
    )
    if existing is not None:
        return existing
    item = PromptVersion(
        id=new_uuid(),
        public_id=new_public_id("prm"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        name=name,
        version=1,
        prompt_sha256=digest,
        template=template,
    )
    session.add(item)
    return item


def _ensure_fake_pricing(session: Session, *, provider: str, model_id: str) -> PricingVersion:
    existing = session.scalar(
        select(PricingVersion)
        .where(
            PricingVersion.provider == provider,
            PricingVersion.model_id == model_id,
        )
        .order_by(PricingVersion.version.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    item = PricingVersion(
        id=new_uuid(),
        public_id=new_public_id("prc"),
        provider=provider,
        model_id=model_id,
        version=1,
        input_usd_micros_per_million=0,
        output_usd_micros_per_million=0,
    )
    session.add(item)
    return item


def _question_digest(
    organisation_id: uuid.UUID, environment_id: uuid.UUID, merchant_account_id: uuid.UUID, text: str
) -> bytes:
    normalized = " ".join(text.casefold().split())
    return hashlib.sha256(
        f"{organisation_id}:{environment_id}:{merchant_account_id}:{normalized}".encode()
    ).digest()


def prepare_question(
    session_factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
    organisation_public_id: str,
    environment_public_id: str,
    question_text: str,
    idempotency_key_digest: bytes,
    now: datetime,
) -> PreparedQuestion:
    if not question_text.strip() or len(question_text) > MAX_QUESTION_LENGTH:
        raise RelayPayError(
            code="SETTLEMENT_QUESTION_INVALID",
            message="Question text must be between 1 and 2000 characters",
            http_status=422,
        )
    digest = _question_digest(organisation_id, environment_id, merchant_account_id, question_text)
    with session_factory() as session, session.begin():
        existing = session.scalar(
            select(SettlementQuestion).where(
                SettlementQuestion.organisation_id == organisation_id,
                SettlementQuestion.environment_id == environment_id,
                SettlementQuestion.question_digest == digest,
            )
        )
        if existing is None:
            existing = session.scalar(
                select(SettlementQuestion).where(
                    SettlementQuestion.organisation_id == organisation_id,
                    SettlementQuestion.environment_id == environment_id,
                    SettlementQuestion.idempotency_key_digest == idempotency_key_digest,
                )
            )
            if existing is not None:
                raise RelayPayError(
                    code="SETTLEMENT_QUESTION_KEY_REUSED",
                    message="Idempotency key was reused with a different question",
                    http_status=409,
                )
        if existing is not None:
            return PreparedQuestion(
                question_id=existing.id,
                question_public_id=existing.public_id,
                run_public_id=_run_public_id(session, existing.workflow_run_id),
                merchant_account_id=existing.merchant_account_id,
                organisation_public_id=organisation_public_id,
                environment_public_id=environment_public_id,
                replayed=True,
            )
        policy = ensure_default_policy(
            session,
            organisation_id=organisation_id,
            environment_id=environment_id,
            merchant_account_id=merchant_account_id,
        )
        definition = _ensure_question_definition(
            session, organisation_id=organisation_id, environment_id=environment_id
        )
        run = WorkflowRun(
            id=new_uuid(),
            public_id=new_public_id("wfr"),
            organisation_id=organisation_id,
            environment_id=environment_id,
            workflow_definition_id=definition.id,
            route="QUESTION:settlement.v1",
            idempotency_digest=digest,
            status="RUNNING",
            token_budget=QUESTION_TOKEN_BUDGET,
            cost_budget_usd_micros=QUESTION_COST_BUDGET_USD_MICROS,
            tokens_used=0,
            cost_used_usd_micros=0,
        )
        session.add(run)
        session.flush([run])
        for step in QUESTION_DEFINITION_STEPS:
            session.add(
                WorkflowStep(
                    id=new_uuid(),
                    public_id=new_public_id("wfs"),
                    organisation_id=organisation_id,
                    environment_id=environment_id,
                    workflow_run_id=run.id,
                    step_key=str(step["key"]),
                    step_kind=str(step["kind"]),
                    definition=step,
                    status="QUEUED",
                    attempt_count=0,
                    max_attempts=3,
                    next_attempt_at=now,
                )
            )
        question = SettlementQuestion(
            id=new_uuid(),
            public_id=new_public_id("sqn"),
            organisation_id=organisation_id,
            environment_id=environment_id,
            merchant_account_id=merchant_account_id,
            policy_id=policy.id,
            workflow_run_id=run.id,
            question_digest=digest,
            idempotency_key_digest=idempotency_key_digest,
            question_text=question_text,
            intent="CLARIFICATION",
            status="PROCESSING",
            classification={},
            classification_sha256=hashlib.sha256(b"{}").digest(),
            asked_at=now,
        )
        session.add(question)
        session.flush([question])
        return PreparedQuestion(
            question_id=question.id,
            question_public_id=question.public_id,
            run_public_id=run.public_id,
            merchant_account_id=merchant_account_id,
            organisation_public_id=organisation_public_id,
            environment_public_id=environment_public_id,
            replayed=False,
        )


def _run_public_id(session: Session, run_id: uuid.UUID) -> str:
    value = session.scalar(select(WorkflowRun.public_id).where(WorkflowRun.id == run_id))
    return value or ""


def _record_model_invocation(
    session: Session,
    *,
    run_id: uuid.UUID,
    step: WorkflowStep,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    result: ModelResult,
    prompt_version: str,
) -> int:
    pricing = _ensure_fake_pricing(session, provider=result.provider, model_id=result.model_id)
    cost = (
        result.input_tokens * pricing.input_usd_micros_per_million
        + result.output_tokens * pricing.output_usd_micros_per_million
    ) // 1_000_000
    session.add(
        ModelInvocation(
            id=new_uuid(),
            public_id=new_public_id("mdl"),
            organisation_id=organisation_id,
            environment_id=environment_id,
            workflow_run_id=run_id,
            workflow_step_id=step.id,
            provider=result.provider,
            model_id=result.model_id,
            prompt_version=prompt_version,
            pricing_version=f"{pricing.provider}/{pricing.model_id}/v{pricing.version}",
            request_sha256=hashlib.sha256(result.request_bytes).digest(),
            response_sha256=hashlib.sha256(result.response_bytes).digest(),
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd_micros=cost,
            finish_status=result.finish_status,
            trace_id=step.public_id[:32],
        )
    )
    return cost


def _complete_step(
    session: Session,
    *,
    run: WorkflowRun,
    step: WorkflowStep,
    output: dict[str, object],
    tokens_used: int,
    cost_usd_micros: int,
    now: datetime,
) -> None:
    step.status = "RUNNING"
    step.attempt_count += 1
    if (
        run.tokens_used + tokens_used > run.token_budget
        or run.cost_used_usd_micros + cost_usd_micros > run.cost_budget_usd_micros
    ):
        step.status = "REQUIRES_REVIEW"
        step.safe_error_code = "MODEL_BUDGET_EXHAUSTED"
        run.status = "REQUIRES_REVIEW"
        return
    run.tokens_used += tokens_used
    run.cost_used_usd_micros += cost_usd_micros
    step.status = "SUCCEEDED"
    step.output = output
    step.completed_at = now
    step.lease_token = None
    step.lease_expires_at = None


def _finalize(
    session_factory: sessionmaker[Session],
    prepared: PreparedQuestion,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    classification: QuestionClassification,
    classification_result: ModelResult,
    result: DeterministicResult | None,
    narrative: SettlementNarrative | None,
    narrative_result: ModelResult | None,
    now: datetime,
) -> dict[str, object]:
    with session_factory() as session, session.begin():
        _ensure_prompt_version(
            session,
            organisation_id=organisation_id,
            environment_id=environment_id,
            name="settlement-classification",
            template=CLASSIFICATION_TEMPLATE,
        )
        _ensure_prompt_version(
            session,
            organisation_id=organisation_id,
            environment_id=environment_id,
            name="settlement-narrative",
            template=NARRATIVE_TEMPLATE,
        )
        question = session.get(SettlementQuestion, prepared.question_id)
        run = session.scalar(
            select(WorkflowRun).where(WorkflowRun.public_id == prepared.run_public_id)
        )
        if question is None or run is None:
            raise not_found("Settlement question")
        steps = {
            step.step_key: step
            for step in session.scalars(
                select(WorkflowStep).where(WorkflowStep.workflow_run_id == run.id)
            ).all()
        }
        classification_payload: dict[str, object] = {
            "intent": classification.intent,
            "dates": [value.isoformat() for value in classification.dates],
            "reason": classification.reason,
            "provider": classification_result.provider,
            "modelId": classification_result.model_id,
        }
        question.intent = classification.intent
        question.classification = classification_payload
        question.classification_sha256 = hashlib.sha256(
            canonical_json_bytes(classification_payload)
        ).digest()
        classify_step = steps.get(CLASSIFICATION_STEP_KEY)
        cost = 0
        if classify_step is not None:
            cost += _record_model_invocation(
                session,
                run_id=run.id,
                step=classify_step,
                organisation_id=organisation_id,
                environment_id=environment_id,
                result=classification_result,
                prompt_version="settlement-classification/v1",
            )
            _complete_step(
                session,
                run=run,
                step=classify_step,
                output={"intent": classification.intent},
                tokens_used=classification_result.input_tokens
                + classification_result.output_tokens,
                cost_usd_micros=cost,
                now=now,
            )
        if classification.intent == "CLARIFICATION" or result is None:
            question.status = "CLARIFICATION"
            answer_payload: dict[str, object] = {
                "intent": "CLARIFICATION",
                "clarification": {
                    "message": (
                        "This question is outside the four supported settlement intents. "
                        "Ask why today's settlement is lower, which payments are still "
                        "unsettled, what the refund cash impact is, or when tomorrow's "
                        "settlement is expected to arrive."
                    ),
                    "supportedIntents": [
                        "SETTLEMENT_LOWER",
                        "UNSETTLED_PAYMENTS",
                        "REFUND_IMPACT",
                        "ARRIVAL_TOMORROW",
                    ],
                },
                "numbers": {},
                "dates": {},
                "calculationSteps": [],
                "citations": [],
                "trace": [],
                "narrative": (
                    "The question is unsupported or ambiguous; rephrase it as one of the "
                    "four supported settlement questions."
                ),
            }
        else:
            if narrative is None or narrative_result is None:
                raise RelayPayError(
                    code="SETTLEMENT_NARRATIVE_MISSING",
                    message="Answered questions require a validated narrative",
                    http_status=500,
                )
            narrative_step = steps.get(NARRATIVE_STEP_KEY)
            if narrative_step is not None:
                narrative_cost = _record_model_invocation(
                    session,
                    run_id=run.id,
                    step=narrative_step,
                    organisation_id=organisation_id,
                    environment_id=environment_id,
                    result=narrative_result,
                    prompt_version="settlement-narrative/v1",
                )
                _complete_step(
                    session,
                    run=run,
                    step=narrative_step,
                    output={"narrative": narrative.narrative},
                    tokens_used=narrative_result.input_tokens + narrative_result.output_tokens,
                    cost_usd_micros=narrative_cost,
                    now=now,
                )
            answer_payload = {
                **result.payload(),
                "narrative": narrative.narrative,
                "citedRecordIds": list(narrative.cited_record_ids),
            }
            question.status = "ANSWERED"
        question.answer = answer_payload
        question.answer_sha256 = hashlib.sha256(canonical_json_bytes(answer_payload)).digest()
        question.answered_at = now
        if run.status == "RUNNING":
            run.status = "SUCCEEDED"
            run.completed_at = now
        append_business_event(
            session,
            organisation_id=organisation_id,
            organisation_public_id=prepared.organisation_public_id,
            environment_id=environment_id,
            environment_public_id=prepared.environment_public_id,
            event_type="settlement-question.answered.v1",
            resource_type="settlement_question",
            resource_id=question.public_id,
            payload={
                "intent": question.intent,
                "status": question.status,
                "merchantAccountId": question.merchant_account_id.hex,
            },
            now=now,
        )
        return {
            "id": question.public_id,
            "status": question.status,
            "intent": question.intent,
            "classification": classification_payload,
            "answer": answer_payload,
            "askedAt": question.asked_at.isoformat(),
        }


def fail_question(
    session_factory: sessionmaker[Session],
    prepared: PreparedQuestion,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    reason_code: str,
    now: datetime,
) -> None:
    with session_factory() as session, session.begin():
        question = session.get(SettlementQuestion, prepared.question_id)
        run = session.scalar(
            select(WorkflowRun).where(WorkflowRun.public_id == prepared.run_public_id)
        )
        if question is None:
            return
        question.status = "FAILED"
        question.answer = {"failureCode": reason_code}
        question.answer_sha256 = hashlib.sha256(
            canonical_json_bytes({"failureCode": reason_code})
        ).digest()
        question.answered_at = now
        if run is not None:
            run.status = "FAILED"
            run.completed_at = now
            for step in session.scalars(
                select(WorkflowStep).where(WorkflowStep.workflow_run_id == run.id)
            ).all():
                if step.status != "SUCCEEDED":
                    step.status = "DEAD_LETTER"
                    step.safe_error_code = reason_code


def answer_question(
    session_factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
    organisation_public_id: str,
    environment_public_id: str,
    question_text: str,
    idempotency_key_digest: bytes,
    provider: StructuredModelProvider,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Full question lifecycle: prepare, classify, query, narrate, validate, finalize."""
    moment = now or datetime.now(UTC)
    prepared = prepare_question(
        session_factory,
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
        organisation_public_id=organisation_public_id,
        environment_public_id=environment_public_id,
        question_text=question_text,
        idempotency_key_digest=idempotency_key_digest,
        now=moment,
    )
    if prepared.replayed:
        with session_factory() as session, session.begin():
            existing = session.get(SettlementQuestion, prepared.question_id)
            if existing is None or existing.answer is None:
                raise not_found("Settlement question")
            return (
                {
                    "id": existing.public_id,
                    "status": existing.status,
                    "intent": existing.intent,
                    "classification": existing.classification,
                    "answer": existing.answer,
                    "askedAt": existing.asked_at.isoformat(),
                },
                True,
            )
    classification_result = provider.generate_structured(
        ModelRequest(
            prompt=classification_prompt(question_text),
            schema=QuestionClassification,
            model_id=QUESTION_MODEL_ID,
            max_output_tokens=512,
            trace_id=prepared.question_public_id,
        )
    )
    classification = classification_result.output
    if not isinstance(classification, QuestionClassification):
        raise RelayPayError(
            code="SETTLEMENT_CLASSIFICATION_INVALID",
            message="Classification provider returned an unsupported schema",
            http_status=502,
        )
    if classification.intent == "CLARIFICATION":
        payload = _finalize(
            session_factory,
            prepared,
            organisation_id=organisation_id,
            environment_id=environment_id,
            classification=classification,
            classification_result=classification_result,
            result=None,
            narrative=None,
            narrative_result=None,
            now=moment,
        )
        return payload, False
    result: DeterministicResult | None = None
    narrative: SettlementNarrative | None = None
    narrative_result: ModelResult | None = None
    try:
        with session_factory() as session, session.begin():
            policy = active_policy(
                session,
                organisation_id=organisation_id,
                environment_id=environment_id,
                merchant_account_id=merchant_account_id,
            )
            if policy is None:
                raise not_found("Settlement policy")
            window = policy_window(policy)
            question_date = resolve_question_date(
                classification, fallback=business_date_of(window, moment)
            )
            result = INTENT_QUERIES[classification.intent](
                session,
                scope=QueryScope(organisation_id, environment_id, merchant_account_id),
                policy=policy,
                window=window,
                question_date=question_date,
                as_of=moment,
            )
        narrative_result = provider.generate_structured(
            ModelRequest(
                prompt=narrative_prompt(result),
                schema=SettlementNarrative,
                model_id=QUESTION_MODEL_ID,
                max_output_tokens=1024,
                trace_id=prepared.question_public_id,
            )
        )
        raw_narrative = narrative_result.output
        if not isinstance(raw_narrative, SettlementNarrative):
            raise RelayPayError(
                code="SETTLEMENT_NARRATIVE_INVALID",
                message="Narrative provider returned an unsupported schema",
                http_status=502,
            )
        narrative = raw_narrative
        validate_narrative(narrative, result)
    except RelayPayError as error:
        fail_question(
            session_factory,
            prepared,
            organisation_id=organisation_id,
            environment_id=environment_id,
            reason_code=error.code,
            now=moment,
        )
        raise
    payload = _finalize(
        session_factory,
        prepared,
        organisation_id=organisation_id,
        environment_id=environment_id,
        classification=classification,
        classification_result=classification_result,
        result=result,
        narrative=narrative,
        narrative_result=narrative_result,
        now=moment,
    )
    return payload, False
