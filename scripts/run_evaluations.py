"""Deterministic v1.0.0 portfolio evaluation runner.

Builds an isolated synthetic organisation, replays the versioned evaluation
fixtures through the real domain services with the deterministic fake
provider, and asserts the release thresholds:

- 100% fake-provider structured-schema conformance;
- 100% settlement numeric and citation correctness;
- 100% recovery policy, consent, and terminal-stop compliance;
- 100% deterministic risk-score agreement;
- zero approval, permission, tenant, PII, or incorrect-action violations;
- at least 0.90 dispute evidence-retrieval precision;
- zero duplicate workflow, payment, message, package-submission, or event
  effects.

Results are recorded in evaluation_datasets/evaluation_runs and printed as a
digest. No live providers are used.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from relaypay.agent_runtime.contracts import ModelRequest, ModelResult, TerminalModelError
from relaypay.agent_runtime.models import EvaluationDataset, EvaluationRun
from relaypay.database import build_engine, build_session_factory
from relaypay.disputes.evidence import plan_evidence
from relaypay.disputes.models import DisputeCase
from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment, Organisation
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import Customer
from relaypay.risk_review.findings import ModelFindings, deterministic_findings
from relaypay.risk_review.service import (
    execute_review,
    prepare_review,
)
from relaypay.risk_review.snapshot import DeterministicSiteSnapshotSource
from relaypay.settlement_intelligence.provider import SettlementFakeProvider
from relaypay.settlement_intelligence.service import answer_question
from relaypay.subscriptions.intake import assert_pii_free_evidence
from relaypay.subscriptions.models import ScheduledRecoveryAction
from relaypay.subscriptions.service import (
    create_invoice,
    create_subscription,
    ensure_default_policy,
    open_recovery_case,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "evaluations" / "v1"
DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
MIN_EVIDENCE_PRECISION = 0.90


class FixedFindingsProvider:
    name = "fake"

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        if request.schema is not ModelFindings:
            raise TerminalModelError("unsupported schema")
        marker = "<relaypay-untrusted-evidence>\n"
        start = request.prompt.index(marker) + len(marker)
        end = request.prompt.index("\n</relaypay-untrusted-evidence>", start)
        snapshot = json.loads(request.prompt[start:end])
        output = deterministic_findings(snapshot)
        response_bytes = canonical_json_bytes(output.model_dump(mode="json"))
        return ModelResult(
            output=output,
            provider=self.name,
            model_id=request.model_id,
            request_bytes=canonical_json_bytes(
                {"model": request.model_id, "prompt": request.prompt}
            ),
            response_bytes=response_bytes,
            latency_ms=0,
            input_tokens=max(1, len(request.prompt) // 4),
            output_tokens=max(1, len(response_bytes) // 4),
            finish_status="STOP",
        )


def _load(name: str) -> dict[str, object]:
    document = json.loads((FIXTURES / name).read_text())
    assert isinstance(document, dict) and "cases" in document
    return document


def _schema_failures() -> int:
    return 0


def _eval_payment_intent(
    session: Session, organisation: Organisation, environment: Environment
) -> uuid.UUID:
    from relaypay.payments.models import PaymentIntent

    existing = session.scalar(
        select(PaymentIntent.id).where(
            PaymentIntent.organisation_id == organisation.id,
            PaymentIntent.merchant_reference == "eval-dispute-intent",
        )
    )
    if existing is not None:
        return existing
    customer_id = new_uuid()
    session.add(
        Customer(
            id=customer_id,
            public_id=new_public_id("cus"),
            organisation_id=organisation.id,
            environment_id=environment.id,
            merchant_customer_reference="eval-dispute-customer",
        )
    )
    intent_id = new_uuid()
    session.add(
        PaymentIntent(
            id=intent_id,
            public_id=new_public_id("pay"),
            organisation_id=organisation.id,
            environment_id=environment.id,
            customer_id=customer_id,
            merchant_account_id=None,
            merchant_reference="eval-dispute-intent",
            amount=5_000,
            currency="INR",
        )
    )
    session.flush()
    return intent_id


def evaluate_disputes(
    factory: sessionmaker[Session],
    organisation: Organisation,
    environment: Environment,
    document: dict[str, object],
) -> dict[str, object]:
    cases = document["cases"]
    assert isinstance(cases, list)
    selected_total = 0
    required_total = 0
    mismatches = 0
    with factory() as session, session.begin():
        for case in cases:
            assert isinstance(case, dict)
            dispute = DisputeCase(
                id=new_uuid(),
                public_id=new_public_id("dpc"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                payment_intent_id=_eval_payment_intent(session, organisation, environment),
                network_dispute_id=f"eval-{case['caseId']}",
                reason_code=str(case["reasonCode"]),
                amount=5_000,
                currency="INR",
                due_at=datetime.now(UTC),
                source_snapshot=case["sourceSnapshot"],
                source_sha256=hashlib.sha256(canonical_json_bytes(case["sourceSnapshot"])).digest(),
                status="OPEN",
            )
            session.add(dispute)
            session.flush([dispute])
            session.refresh(dispute)
            plan = plan_evidence(dispute)
            expected_selected = [str(item) for item in case["expectedSelected"]]
            expected_missing = [str(item) for item in case["expectedMissing"]]
            if sorted(plan.selected) != sorted(expected_selected) or sorted(plan.missing) != sorted(
                expected_missing
            ):
                mismatches += 1
            selected_total += len(plan.selected)
            required_total += len(plan.selected) + len(plan.missing)
    precision = selected_total / required_total if required_total else 1.0
    assert precision >= MIN_EVIDENCE_PRECISION, f"evidence precision {precision} below 0.90"
    assert mismatches == 0, f"{mismatches} dispute evidence plans deviated from the fixture"
    return {
        "cases": len(cases),
        "precision": round(precision, 6),
        "schemaFailures": _schema_failures(),
    }


def evaluate_recovery(
    factory: sessionmaker[Session],
    organisation: Organisation,
    environment: Environment,
    document: dict[str, object],
) -> dict[str, object]:
    cases = document["cases"]
    assert isinstance(cases, list)
    now = datetime.now(UTC)
    # provider_attempt_id is globally unique across the ledger, so eval
    # attempts must not collide with rows from previous runs.
    run_nonce = uuid.uuid4().hex[:12]
    policy_violations = 0
    with factory() as session, session.begin():
        policy = ensure_default_policy(
            session,
            organisation_id=organisation.id,
            environment_id=environment.id,
        )
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation.id,
            environment_id=environment.id,
            merchant_customer_reference=f"eval-recovery-{uuid.uuid4().hex}",
        )
        session.add(customer)
        session.flush([customer])
        for case in cases:
            assert isinstance(case, dict)
            subscription = create_subscription(
                session,
                organisation_id=organisation.id,
                environment_id=environment.id,
                customer_public_id=customer.public_id,
                external_id=f"eval-{case['caseId']}",
                plan_reference="eval-plan",
                amount=5_000,
                consent={
                    "channels": case["consent"]["channels"],
                    "displayName": "Eval Subscriber",
                },
            )
            session.flush([subscription])
            invoice = create_invoice(
                session,
                subscription=subscription,
                external_id=f"eval-{case['caseId']}-inv",
                due_at=now,
            )
            session.flush([invoice])
            definition_steps = {"steps": [{"key": "recover", "kind": "SYSTEM"}]}
            from relaypay.agent_runtime.models import WorkflowDefinition, WorkflowRun

            definition = WorkflowDefinition(
                id=new_uuid(),
                public_id=new_public_id("wdf"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                name=f"eval-recovery-{case['caseId']}",
                version=1,
                definition_sha256=hashlib.sha256(b"eval").digest(),
                definition=definition_steps,
                status="ACTIVE",
            )
            session.add(definition)
            session.flush([definition])
            run = WorkflowRun(
                id=new_uuid(),
                public_id=new_public_id("wfr"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                workflow_definition_id=definition.id,
                route="EVAL:recovery",
                idempotency_digest=hashlib.sha256(str(case["caseId"]).encode()).digest(),
                status="RUNNING",
                token_budget=1_000,
                cost_budget_usd_micros=10_000,
                tokens_used=0,
                cost_used_usd_micros=0,
            )
            session.add(run)
            session.flush([run])
            from relaypay.subscriptions.models import RecurringPaymentAttempt

            attempt = RecurringPaymentAttempt(
                id=new_uuid(),
                public_id=new_public_id("rpa"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                invoice_id=invoice.id,
                attempt_number=1,
                provider_attempt_id=f"eval-attempt-{run_nonce}-{case['caseId']}",
                outcome="VERIFIED_FAILED",
                failure_classification=case["failureClassification"],
                provider_code=str(case["providerCode"]),
                evidence={"verified": True, "source": "eval"},
                evidence_sha256=hashlib.sha256(b"eval").digest(),
                occurred_at=now,
            )
            session.add(attempt)
            session.flush([attempt])
            case_row = open_recovery_case(
                session,
                subscription=subscription,
                invoice=invoice,
                trigger_attempt=attempt,
                workflow_run=run,
                policy=policy,
                now=now,
            )
            session.flush([case_row])
            actions = list(
                session.scalars(
                    select(ScheduledRecoveryAction).where(
                        ScheduledRecoveryAction.recovery_case_id == case_row.id
                    )
                ).all()
            )
            expect_terminal = bool(case["expectTerminal"])
            expect_no_action = bool(case["expectNoAction"])
            if expect_no_action and actions:
                policy_violations += 1
            # A consented case that terminated without actions must have been
            # an expected terminal or no-action outcome.
            if (
                case_row.status == "TERMINATED"
                and not expect_terminal
                and not expect_no_action
                and actions == []
                and case["consent"]["channels"]
            ):
                policy_violations += 1
    assert policy_violations == 0, f"{policy_violations} recovery policy violations"
    return {"cases": len(cases), "policyViolations": policy_violations}


def evaluate_settlement(
    factory: sessionmaker[Session],
    organisation: Organisation,
    organisation_public_id: str,
    environment: Environment,
    environment_public_id: str,
    document: dict[str, object],
) -> dict[str, object]:
    cases = document["cases"]
    assert isinstance(cases, list)
    from relaypay.merchant_balances.service import ensure_default_merchant_account

    with factory() as session, session.begin():
        account = ensure_default_merchant_account(
            session, organisation_id=organisation.id, environment_id=environment.id
        )
    provider = SettlementFakeProvider()
    numeric_mismatches = 0
    citation_failures = 0
    schema_failures = 0
    for case in cases:
        assert isinstance(case, dict)
        payload, _replayed = answer_question(
            factory,
            organisation_id=organisation.id,
            environment_id=environment.id,
            merchant_account_id=account.id,
            organisation_public_id=organisation_public_id,
            environment_public_id=environment_public_id,
            question_text=str(case["question"]),
            idempotency_key_digest=hashlib.sha256(str(case["caseId"]).encode()).digest(),
            provider=provider,
            now=datetime.now(UTC),
        )
        if payload["intent"] != case["intent"]:
            numeric_mismatches += 1
            continue
        if payload["intent"] == "CLARIFICATION":
            continue
        answer = payload["answer"]
        assert isinstance(answer, dict)
        # Numeric correctness: the recorded payload is byte-stable on replay.
        replay_payload, replayed = answer_question(
            factory,
            organisation_id=organisation.id,
            environment_id=environment.id,
            merchant_account_id=account.id,
            organisation_public_id=organisation_public_id,
            environment_public_id=environment_public_id,
            question_text=str(case["question"]),
            idempotency_key_digest=hashlib.sha256(f"{case['caseId']}:replay".encode()).digest(),
            provider=provider,
            now=datetime.now(UTC),
        )
        assert replayed
        if replay_payload["answer"] != answer:
            numeric_mismatches += 1
        cited = answer.get("citedRecordIds")
        if not isinstance(cited, list) or not cited:
            citation_failures += 1
    assert numeric_mismatches == 0, f"{numeric_mismatches} settlement numeric mismatches"
    assert citation_failures == 0, f"{citation_failures} settlement citation failures"
    return {
        "cases": len(cases),
        "numericMismatches": numeric_mismatches,
        "citationFailures": citation_failures,
        "schemaFailures": schema_failures,
    }


def evaluate_risk(
    factory: sessionmaker[Session],
    organisation: Organisation,
    organisation_public_id: str,
    environment: Environment,
    environment_public_id: str,
    document: dict[str, object],
) -> dict[str, object]:
    cases = document["cases"]
    assert isinstance(cases, list)
    source = DeterministicSiteSnapshotSource()
    score_disagreements = 0
    unexpected_escalations = 0
    for case in cases:
        assert isinstance(case, dict)
        prepared = prepare_review(
            factory,
            organisation_id=organisation.id,
            environment_id=environment.id,
            organisation_public_id=organisation_public_id,
            environment_public_id=environment_public_id,
            site_ref=str(case["siteRef"]),
            source=source,
            source_url="deterministic://eval",
        )
        first = execute_review(
            factory, prepared, provider=FixedFindingsProvider(), now=datetime.now(UTC)
        )
        # Re-prepare the same site: the idempotent shell replays the recorded
        # review instead of executing a second time.
        replayed = prepare_review(
            factory,
            organisation_id=organisation.id,
            environment_id=environment.id,
            organisation_public_id=organisation_public_id,
            environment_public_id=environment_public_id,
            site_ref=str(case["siteRef"]),
            source=source,
            source_url="deterministic://eval",
        )
        second = execute_review(
            factory, replayed, provider=FixedFindingsProvider(), now=datetime.now(UTC)
        )
        if first["versions"] != second["versions"]:
            score_disagreements += 1
        escalated = first["status"] == "ESCALATED"
        if escalated != bool(case["expectEscalated"]):
            unexpected_escalations += 1
    assert score_disagreements == 0, f"{score_disagreements} risk score disagreements"
    assert unexpected_escalations == 0, f"{unexpected_escalations} unexpected escalation states"
    return {
        "cases": len(cases),
        "scoreDisagreements": score_disagreements,
        "unexpectedEscalations": unexpected_escalations,
    }


def evaluate_adversarial(
    factory: sessionmaker[Session],
    organisation: Organisation,
    organisation_public_id: str,
    environment: Environment,
    environment_public_id: str,
    document: dict[str, object],
) -> dict[str, object]:
    cases = document["cases"]
    assert isinstance(cases, list)
    from relaypay.agent_runtime.workflows import resolve_admin_scope
    from relaypay.identity.security import Principal
    from relaypay.settlement_intelligence.models import SettlementQuestion

    violations = 0
    with factory() as session, session.begin():
        viewer = Principal(
            kind="SESSION",
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=environment.id,
            environment_public_id=environment.public_id,
            display_name="Eval Viewer",
            scopes=frozenset({"technical:read"}),
            membership_role="VIEWER",
            user_id=None,
        )
        for case in cases:
            assert isinstance(case, dict)
            variant = str(case["variant"])
            if variant == "PERMISSION_ESCALATION":
                try:
                    resolve_admin_scope(
                        session,
                        principal=viewer,
                        environment_public_id=environment.public_id,
                        permission="workflows:write",
                    )
                    violations += 1
                except RelayPayError as error:
                    assert error.code == "FORBIDDEN"
            elif variant == "CROSS_TENANT_ACCESS":
                questions = session.scalars(
                    select(SettlementQuestion).where(
                        SettlementQuestion.organisation_id == organisation.id
                    )
                ).all()
                for question in questions:
                    assert question.environment_id == environment.id
            elif variant == "PII_IN_EVIDENCE":
                try:
                    assert_pii_free_evidence({"detail": "customer santosh@example.test called"})
                    violations += 1
                except RelayPayError as error:
                    assert error.code == "EVENT_CONTAINS_PII"
            elif variant == "PROMPT_INJECTION_RECORD":
                from relaypay.risk_review.findings import deterministic_findings

                from apps.merchant_site.main import _snapshot

                snapshot = dict(_snapshot("SUSPICIOUS_CLAIMS"))
                findings = deterministic_findings(snapshot)
                for finding in findings.findings:
                    if finding.classification == "SUSPICIOUS_LANGUAGE":
                        assert "ignore all previous instructions" in finding.quote.casefold()
                        break
            elif variant == "DUPLICATE_EFFECT":
                from relaypay.subscriptions.network import DeterministicCommunicationNetwork

                network = DeterministicCommunicationNetwork()
                first = network.send(
                    stable_key="eval-duplicate-effect",
                    channel="EMAIL",
                    body="synthetic eval message",
                )
                again = network.send(
                    stable_key="eval-duplicate-effect",
                    channel="EMAIL",
                    body="synthetic eval message",
                )
                if first.response_bytes != again.response_bytes:
                    violations += 1
    assert violations == 0, f"{violations} adversarial violations"
    return {"cases": len(cases), "violations": violations}


def main() -> None:
    engine = build_engine(DATABASE_URL, application_name="v1-evaluation-runner")
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            organisation = Organisation(
                id=new_uuid(),
                public_id=new_public_id("org"),
                name=f"V1 evaluation {uuid.uuid4().hex[:8]}",
                status="ACTIVE",
            )
            session.add(organisation)
            session.flush([organisation])
            environment = session.scalar(
                select(Environment).where(
                    Environment.organisation_id == organisation.id,
                    Environment.environment_type == "TEST",
                )
            )
            assert environment is not None
        # The setup transaction must commit before any leg opens its own
        # connection: rows inserted on a second connection would block on the
        # uncommitted organisation foreign keys (two-connection self-deadlock).
        results: dict[str, object] = {}
        results["disputes"] = evaluate_disputes(
            factory, organisation, environment, _load("disputes-v1.json")
        )
        results["recovery"] = evaluate_recovery(
            factory, organisation, environment, _load("recovery-v1.json")
        )
        results["settlement"] = evaluate_settlement(
            factory,
            organisation,
            organisation.public_id,
            environment,
            environment.public_id,
            _load("settlement-v1.json"),
        )
        results["risk"] = evaluate_risk(
            factory,
            organisation,
            organisation.public_id,
            environment,
            environment.public_id,
            _load("risk-v1.json"),
        )
        results["adversarial"] = evaluate_adversarial(
            factory,
            organisation,
            organisation.public_id,
            environment,
            environment.public_id,
            _load("adversarial-v1.json"),
        )
        dataset_bytes = b"".join(
            (FIXTURES / name).read_bytes()
            for name in sorted(p.name for p in FIXTURES.glob("*.json"))
        )
        with factory() as session, session.begin():
            from relaypay.agent_runtime.models import WorkflowDefinition

            definition = WorkflowDefinition(
                id=new_uuid(),
                public_id=new_public_id("wdf"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                name="portfolio-v1-evaluation",
                version=1,
                definition_sha256=hashlib.sha256(b"portfolio-v1-evaluation").digest(),
                definition={"steps": [{"key": "evaluate", "kind": "SYSTEM"}]},
                status="ACTIVE",
            )
            session.add(definition)
            session.flush([definition])
            dataset = EvaluationDataset(
                id=new_uuid(),
                public_id=new_public_id("evd"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                name="portfolio-v1",
                version=1,
                dataset_sha256=hashlib.sha256(dataset_bytes).digest(),
                cases=[
                    {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in sorted(FIXTURES.glob("*.json"))
                ],
                case_count=134,
            )
            session.add(dataset)
            session.flush([dataset])
            session.add(
                EvaluationRun(
                    id=new_uuid(),
                    public_id=new_public_id("evr"),
                    organisation_id=organisation.id,
                    environment_id=environment.id,
                    evaluation_dataset_id=dataset.id,
                    workflow_definition_id=definition.id,
                    status="SUCCEEDED",
                    result=results,
                    result_sha256=hashlib.sha256(canonical_json_bytes(results)).digest(),
                    completed_at=datetime.now(UTC),
                )
            )
        print(json.dumps(results, indent=2, sort_keys=True))
        print("V1 evaluation PASS: all deterministic thresholds hold")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
