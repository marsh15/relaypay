# ruff: noqa: S608 -- table names interpolated below come from fixed in-file allowlists
"""Migration 0018: tables created in 0012-0017 are trigger-protected where code never mutates them.

Every protected table is seeded with one row through the ORM inside a transaction that is rolled
back at the end of each test, so nothing leaks into the shared database. Class A tables reject
any UPDATE and DELETE; class B tables reject DELETE and UPDATE of frozen columns but still allow
the documented lifecycle column; class C tables (leased or status-machine rows) stay mutable.
The SQLSTATE is ``55000`` throughout, the code ``relaypay_prevent_evidence_mutation`` uses.
"""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.models import (
    ApprovalDecision,
    ApprovalRequest,
    BusinessEventOutbox,
    CommunicationRecord,
    ConsumedBusinessEvent,
    Customer,
    DeadLetter,
    DisputeCase,
    DisputeDraftVersion,
    DisputePackageVersion,
    EvaluationDataset,
    EvaluationRun,
    ModelInvocation,
    OnboardingSnapshot,
    PaymentIntent,
    PricingVersion,
    PromptVersion,
    RecoveryCase,
    RecoveryOptOut,
    RecoveryPolicyVersion,
    RecurringPaymentAttempt,
    RiskAnnotation,
    RiskEscalation,
    RiskFinding,
    RiskReview,
    RiskReviewVersion,
    ScheduledRecoveryAction,
    SettlementForecast,
    SettlementForecastItem,
    SettlementPolicy,
    Subscription,
    SubscriptionInvoice,
    ToolInvocation,
    WorkflowArtifact,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.world import World, build_world, create_payment

pytestmark = pytest.mark.integration

IMMUTABILITY_SQLSTATE = "55000"

# Class A: full-row trigger (relaypay_prevent_evidence_mutation).
FULL_IMMUTABLE = (
    "pricing_versions",
    "consumed_business_events",
    "evaluation_datasets",
    "prompt_versions",
    "workflow_artifacts",
    "model_invocations",
    "tool_invocations",
    "approval_decisions",
    "dispute_draft_versions",
    "recurring_payment_attempts",
    "recovery_opt_outs",
    "settlement_forecasts",
    "settlement_forecast_items",
    "onboarding_snapshots",
    "risk_review_versions",
    "risk_findings",
    "risk_annotations",
)

# Class B: table -> (columns that may still change, an UPDATE that changes only those).
COLUMN_SCOPED: dict[str, tuple[tuple[str, ...], str]] = {
    "workflow_definitions": (("status",), "status = 'RETIRED'"),
    "evaluation_runs": (
        ("status", "result", "result_sha256", "completed_at"),
        "status = 'SUCCEEDED', completed_at = now()",
    ),
    "workflow_dead_letters": (
        ("replay_count", "last_replayed_at"),
        "replay_count = replay_count + 1, last_replayed_at = now()",
    ),
    "dispute_package_versions": (("status",), "status = 'INVALIDATED'"),
    "recovery_policy_versions": (("status",), "status = 'RETIRED'"),
    "communication_records": (
        ("status", "response_code", "response_sha256"),
        "status = 'DELIVERED', response_code = 'OK'",
    ),
    "settlement_policies": (("status",), "status = 'RETIRED'"),
}

# Class C: mutable workflow state; must stay trigger-free. Value is a legitimate UPDATE.
MUTABLE: dict[str, str] = {
    "business_event_outbox": "publish_attempts = publish_attempts + 1, published_at = now()",
    "workflow_runs": "status = 'SUCCEEDED', completed_at = now()",
    "workflow_steps": "status = 'SUCCEEDED', completed_at = now()",
    "approval_requests": "status = 'APPROVED'",
    "dispute_cases": "status = 'DRAFTED'",
    "dispute_submission_attempts": "status = 'SUCCEEDED'",
    "subscriptions": "status = 'PAST_DUE'",
    "subscription_invoices": "status = 'FAILED'",
    "recovery_cases": "status = 'TERMINATED', terminated_at = now()",
    "scheduled_recovery_actions": "status = 'CANCELLED'",
    "settlement_questions": "status = 'ANSWERED', answered_at = now()",
    "risk_reviews": "status = 'COMPLETED'",
    "risk_escalations": "status = 'DISPOSITIONED'",
}
# request_logs and usage_rollups (class C, written by the HTTP middleware) are covered by
# test_m7_observability, which deletes and upserts them.
UNSEEDED_MUTABLE = {"dispute_submission_attempts", "settlement_questions"}

ALL_PROTECTED = (*FULL_IMMUTABLE, *COLUMN_SCOPED)


@pytest.fixture(scope="module")
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("m15-immutability-tests") as session_factory:
        yield session_factory


@pytest.fixture(scope="module")
def world(factory: sessionmaker[Session]) -> World:
    return build_world(factory, label="m15-immutability")


@pytest.fixture(scope="module")
def payment_intent_id(factory: sessionmaker[Session], world: World) -> uuid.UUID:
    public_id = create_payment(factory, world, amount=50_000)
    with factory() as session, session.begin():
        row = session.scalar(select(PaymentIntent.id).where(PaymentIntent.public_id == public_id))
        assert row is not None
        return row


@pytest.fixture(scope="module")
def customer_id(factory: sessionmaker[Session], world: World) -> uuid.UUID:
    with factory() as session, session.begin():
        row = session.scalar(
            select(Customer.id).where(Customer.public_id == world.customer_public_id)
        )
        assert row is not None
        return row


def _sha(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def _pid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _seed(
    session: Session, world: World, payment_intent_id: uuid.UUID, customer_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """Insert one representative row per 0012-0017 table (except request logs and rollups)."""
    scope = {"organisation_id": world.organisation_id, "environment_id": world.environment_id}
    ids: dict[str, uuid.UUID] = {}
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex

    def add(table: str, row: object) -> uuid.UUID:
        session.add(row)
        session.flush()
        row_id: uuid.UUID = row.id  # type: ignore[attr-defined]
        ids[table] = row_id
        return row_id

    add(
        "pricing_versions",
        PricingVersion(
            public_id=_pid("prv"),
            provider="fixture",
            model_id=f"model-{tag}",
            version=1,
            input_usd_micros_per_million=1,
            output_usd_micros_per_million=2,
        ),
    )
    add(
        "consumed_business_events",
        ConsumedBusinessEvent(
            **scope,
            consumer_name=f"consumer-{tag}",
            event_id=f"evt_{tag}",
            payload_sha256=_sha(tag),
        ),
    )
    dataset_id = add(
        "evaluation_datasets",
        EvaluationDataset(
            **scope,
            public_id=_pid("eds"),
            name=f"dataset-{tag}",
            version=1,
            dataset_sha256=_sha(f"ds-{tag}"),
            cases=[],
            case_count=0,
        ),
    )
    add(
        "prompt_versions",
        PromptVersion(
            **scope,
            public_id=_pid("pmt"),
            name=f"prompt-{tag}",
            version=1,
            prompt_sha256=_sha(f"pt-{tag}"),
            template="synthetic template",
        ),
    )
    definition_id = add(
        "workflow_definitions",
        WorkflowDefinition(
            **scope,
            public_id=_pid("wdf"),
            name=f"workflow-{tag}",
            version=1,
            definition_sha256=_sha(f"wd-{tag}"),
            definition={},
            status="ACTIVE",
        ),
    )
    add(
        "evaluation_runs",
        EvaluationRun(
            **scope,
            public_id=_pid("evr"),
            evaluation_dataset_id=dataset_id,
            workflow_definition_id=definition_id,
            status="RUNNING",
        ),
    )
    run_id = add(
        "workflow_runs",
        WorkflowRun(
            **scope,
            public_id=_pid("wfr"),
            workflow_definition_id=definition_id,
            route=f"route-{tag}",
            idempotency_digest=_sha(f"idem-{tag}"),
            status="RUNNING",
            token_budget=1000,
            cost_budget_usd_micros=1000,
        ),
    )
    step_id = add(
        "workflow_steps",
        WorkflowStep(
            **scope,
            public_id=_pid("wfs"),
            workflow_run_id=run_id,
            step_key="step",
            step_kind="SYSTEM",
            definition={},
            status="RUNNING",
            max_attempts=3,
            next_attempt_at=now,
        ),
    )
    artifact_id = add(
        "workflow_artifacts",
        WorkflowArtifact(
            **scope,
            public_id=_pid("wfa"),
            workflow_run_id=run_id,
            artifact_type="fixture",
            version=1,
            media_type="application/json",
            content_bytes=b"{}",
            content_sha256=_sha("{}"),
            byte_length=2,
        ),
    )
    request_id = add(
        "approval_requests",
        ApprovalRequest(
            **scope,
            public_id=_pid("apr"),
            workflow_run_id=run_id,
            artifact_id=artifact_id,
            artifact_sha256=_sha("{}"),
            maker_user_id=uuid.uuid4(),
            status="PENDING",
        ),
    )
    add(
        "approval_decisions",
        ApprovalDecision(
            **scope,
            public_id=_pid("apd"),
            approval_request_id=request_id,
            maker_user_id=uuid.uuid4(),
            decision_maker_user_id=uuid.uuid4(),
            decision="APPROVED",
        ),
    )
    add(
        "model_invocations",
        ModelInvocation(
            **scope,
            public_id=_pid("mdi"),
            workflow_run_id=run_id,
            workflow_step_id=step_id,
            provider="fixture",
            model_id="fixture-model",
            prompt_version="pmt:1",
            pricing_version="prv:1",
            request_sha256=_sha("req"),
            response_sha256=_sha("res"),
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            cost_usd_micros=1,
            finish_status="STOP",
            trace_id=uuid.uuid4().hex,
        ),
    )
    add(
        "tool_invocations",
        ToolInvocation(
            **scope,
            public_id=_pid("tli"),
            workflow_run_id=run_id,
            workflow_step_id=step_id,
            tool_name="fixture",
            tool_version=1,
            input_sha256=_sha("in"),
            output_sha256=_sha("out"),
            latency_ms=1,
            result_bytes=1,
            trace_id=uuid.uuid4().hex,
        ),
    )
    add(
        "workflow_dead_letters",
        DeadLetter(
            **scope,
            public_id=_pid("wdl"),
            workflow_run_id=run_id,
            workflow_step_id=step_id,
            reason_code="FIXTURE",
            evidence={},
        ),
    )
    add(
        "business_event_outbox",
        BusinessEventOutbox(
            **scope,
            event_id=f"evt_{tag}_outbox",
            event_type="fixture.v1",
            schema_version=1,
            occurred_at=now,
            resource_type="fixture",
            resource_id=tag,
            topic="fixture",
            partition_key=tag,
            payload={},
            payload_sha256=_sha("p"),
            event_bytes=b"{}",
            next_attempt_at=now,
        ),
    )

    case_id = add(
        "dispute_cases",
        DisputeCase(
            **scope,
            public_id=_pid("dsp"),
            payment_intent_id=payment_intent_id,
            network_dispute_id=f"net-{tag}",
            reason_code="FRAUD",
            amount=1000,
            currency="INR",
            due_at=now + timedelta(days=7),
            source_snapshot={},
            source_sha256=_sha("src"),
            status="OPEN",
        ),
    )
    draft_id = add(
        "dispute_draft_versions",
        DisputeDraftVersion(
            **scope,
            public_id=_pid("ddv"),
            dispute_case_id=case_id,
            version=1,
            author_type="ANALYST",
            classification="FRAUD",
            confidence="LOW",
            response_text="synthetic",
            selected_evidence=[],
            missing_evidence=[],
            content_sha256=_sha("draft"),
        ),
    )
    add(
        "dispute_package_versions",
        DisputePackageVersion(
            **scope,
            public_id=_pid("dpv"),
            dispute_case_id=case_id,
            draft_version_id=draft_id,
            workflow_artifact_id=artifact_id,
            version=1,
            manifest={},
            package_bytes=b"zip",
            package_sha256=_sha(f"pkg-{tag}"),
            byte_length=3,
            status="FROZEN",
        ),
    )

    subscription_id = add(
        "subscriptions",
        Subscription(
            **scope,
            public_id=_pid("sub"),
            customer_id=customer_id,
            external_id=f"sub-{tag}",
            plan_reference="plan",
            amount=1000,
            currency="INR",
            consent={},
            consent_sha256=_sha("consent"),
            status="ACTIVE",
        ),
    )
    invoice_id = add(
        "subscription_invoices",
        SubscriptionInvoice(
            **scope,
            public_id=_pid("inv"),
            subscription_id=subscription_id,
            external_id=f"inv-{tag}",
            amount=1000,
            currency="INR",
            due_at=now,
            status="OPEN",
        ),
    )
    attempt_id = add(
        "recurring_payment_attempts",
        RecurringPaymentAttempt(
            **scope,
            public_id=_pid("rpa"),
            invoice_id=invoice_id,
            attempt_number=1,
            provider_attempt_id=f"pa-{tag}",
            outcome="VERIFIED_FAILED",
            failure_classification="RETRYABLE_SOFT_DECLINE",
            provider_code="DO_NOT_HONOR",
            evidence={},
            evidence_sha256=_sha("evidence"),
            occurred_at=now,
        ),
    )
    policy_version_id = add(
        "recovery_policy_versions",
        RecoveryPolicyVersion(
            **scope,
            public_id=_pid("rpv"),
            version=1,
            max_payment_retries=1,
            max_messages=1,
            window_days=1,
            retry_windows_hours=[1],
            message_windows_hours=[1],
            policy_sha256=_sha("policy"),
            status="ACTIVE",
        ),
    )
    recovery_case_id = add(
        "recovery_cases",
        RecoveryCase(
            **scope,
            public_id=_pid("rcs"),
            subscription_id=subscription_id,
            invoice_id=invoice_id,
            trigger_attempt_id=attempt_id,
            policy_version_id=policy_version_id,
            workflow_run_id=run_id,
            status="OPEN",
            classification="RETRYABLE_SOFT_DECLINE",
            payment_retry_count=0,
            message_count=0,
            started_at=now,
            expires_at=now + timedelta(days=1),
        ),
    )
    action_id = add(
        "scheduled_recovery_actions",
        ScheduledRecoveryAction(
            **scope,
            public_id=_pid("sra"),
            recovery_case_id=recovery_case_id,
            sequence=1,
            action_type="MESSAGE",
            channel="EMAIL",
            scheduled_for=now,
            stable_key=f"action-{tag}",
            payload={},
            payload_sha256=_sha("action"),
            status="SCHEDULED",
        ),
    )
    add(
        "communication_records",
        CommunicationRecord(
            **scope,
            public_id=_pid("com"),
            recovery_case_id=recovery_case_id,
            scheduled_action_id=action_id,
            stable_key=f"comm-{tag}",
            channel="EMAIL",
            tokenized_body="hello",
            rendered_body="hello",
            request_sha256=_sha("comm"),
            status="SENT",
        ),
    )
    add(
        "recovery_opt_outs",
        RecoveryOptOut(
            **scope,
            public_id=_pid("opt"),
            recovery_case_id=recovery_case_id,
            source_event_id=f"opt-{tag}",
            channel="ALL",
            received_at=now,
        ),
    )

    policy_id = add(
        "settlement_policies",
        SettlementPolicy(
            **scope,
            public_id=_pid("spv"),
            merchant_account_id=world.merchant_account_id,
            version=1,
            timezone_name="Asia/Kolkata",
            cutoff_hour=17,
            cutoff_minute=0,
            settlement_delay_days=1,
            weekend_handling="SKIP",
            policy_sha256=_sha(f"sp-{tag}"),
            status="ACTIVE",
        ),
    )
    forecast_id = add(
        "settlement_forecasts",
        SettlementForecast(
            **scope,
            public_id=_pid("sfc"),
            merchant_account_id=world.merchant_account_id,
            policy_id=policy_id,
            business_date=now.date(),
            sequence=1,
            cutoff_at=now,
            expected_arrival_date=now.date(),
            capture_total=100,
            refund_total=0,
            receivable_offset_total=100,
            expected_settlement_amount=0,
            capture_count=0,
            refund_count=0,
            currency="INR",
            snapshot={},
            snapshot_sha256=_sha("forecast"),
        ),
    )
    add(
        "settlement_forecast_items",
        SettlementForecastItem(
            **scope,
            public_id=_pid("sfi"),
            forecast_id=forecast_id,
            item_type="RECEIVABLE_OFFSET",
            amount=-100,
            currency="INR",
        ),
    )

    snapshot_id = add(
        "onboarding_snapshots",
        OnboardingSnapshot(
            **scope,
            public_id=_pid("obs"),
            site_ref=f"site-{tag}",
            snapshot={},
            snapshot_sha256=_sha(f"snap-{tag}"),
            source_url="https://example.test",
        ),
    )
    review_id = add(
        "risk_reviews",
        RiskReview(
            **scope,
            public_id=_pid("rrv"),
            onboarding_snapshot_id=snapshot_id,
            status="PENDING",
            score_version=1,
        ),
    )
    version_id = add(
        "risk_review_versions",
        RiskReviewVersion(
            **scope,
            public_id=_pid("rvv"),
            risk_review_id=review_id,
            version=1,
            score_version=1,
            completeness_score=1,
            domain_score=1,
            category_score=1,
            pricing_score=1,
            claims_score=1,
            total_score=5,
            severity="LOW",
            confidence="HIGH",
            breakdown={},
            review_sha256=_sha("review"),
        ),
    )
    add(
        "risk_findings",
        RiskFinding(
            **scope,
            public_id=_pid("rfd"),
            risk_review_id=review_id,
            position=1,
            finding_type="DETERMINISTIC",
            check_key="fixture",
            classification="NONE",
            severity="NONE",
            confidence="HIGH",
            detail="synthetic",
            snapshot_sha256=_sha("snap"),
        ),
    )
    add(
        "risk_escalations",
        RiskEscalation(
            **scope,
            public_id=_pid("res"),
            risk_review_id=review_id,
            risk_review_version_id=version_id,
            reason="HIGH_SEVERITY",
            status="OPEN",
        ),
    )
    add(
        "risk_annotations",
        RiskAnnotation(
            **scope,
            public_id=_pid("ran"),
            risk_review_id=review_id,
            author_user_id=uuid.uuid4(),
            note="synthetic note",
        ),
    )
    return ids


@pytest.fixture
def seeded(
    factory: sessionmaker[Session],
    world: World,
    payment_intent_id: uuid.UUID,
    customer_id: uuid.UUID,
) -> Iterator[tuple[Session, dict[str, uuid.UUID]]]:
    """A session inside an open transaction holding one row per table; always rolled back."""
    session = factory()
    transaction = session.begin()
    try:
        yield session, _seed(session, world, payment_intent_id, customer_id)
    finally:
        transaction.rollback()
        session.close()


def _expect_immutable(session: Session, statement: str, row_id: uuid.UUID) -> None:
    with pytest.raises(DBAPIError) as raised, session.begin_nested():
        session.execute(text(statement), {"id": row_id})
    assert getattr(raised.value.orig, "sqlstate", None) == IMMUTABILITY_SQLSTATE
    assert "immutable" in str(raised.value.orig)


def _triggers(session: Session, table: str) -> dict[str, str]:
    """User trigger name -> trigger function for ``table``."""
    rows = session.execute(
        text(
            """
            SELECT t.tgname, p.proname
            FROM pg_trigger t
            JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE t.tgrelid = to_regclass(:table) AND NOT t.tgisinternal
            """
        ),
        {"table": table},
    )
    return {name: function for name, function in rows}


@pytest.mark.parametrize("table", FULL_IMMUTABLE)
def test_full_row_tables_reject_update_and_delete(
    seeded: tuple[Session, dict[str, uuid.UUID]], table: str
) -> None:
    session, ids = seeded
    # created_at exists on every table and is never a lifecycle column.
    _expect_immutable(
        session,
        f"UPDATE {table} SET created_at = created_at + interval '1 second' WHERE id = :id",
        ids[table],
    )
    _expect_immutable(session, f"DELETE FROM {table} WHERE id = :id", ids[table])
    assert _triggers(session, table) == {f"{table}_immutable": "relaypay_prevent_evidence_mutation"}


@pytest.mark.parametrize("table", COLUMN_SCOPED)
def test_column_scoped_tables_freeze_everything_but_the_lifecycle_columns(
    seeded: tuple[Session, dict[str, uuid.UUID]], table: str
) -> None:
    session, ids = seeded
    _mutable_columns, lifecycle_update = COLUMN_SCOPED[table]
    _expect_immutable(
        session,
        f"UPDATE {table} SET created_at = created_at + interval '1 second' WHERE id = :id",
        ids[table],
    )
    _expect_immutable(session, f"DELETE FROM {table} WHERE id = :id", ids[table])
    # The documented lifecycle transition still works.
    with session.begin_nested():
        result = session.execute(
            text(f"UPDATE {table} SET {lifecycle_update} WHERE id = :id"),
            {"id": ids[table]},
        )
    assert result.rowcount == 1
    assert _triggers(session, table) == {
        f"{table}_frozen_columns": "relaypay_prevent_frozen_column_mutation"
    }


def test_frozen_content_columns_are_rejected_even_next_to_a_lifecycle_change(
    seeded: tuple[Session, dict[str, uuid.UUID]],
) -> None:
    session, ids = seeded
    _expect_immutable(
        session,
        "UPDATE dispute_package_versions SET status = 'INVALIDATED', package_bytes = 'x' "
        "WHERE id = :id",
        ids["dispute_package_versions"],
    )
    _expect_immutable(
        session,
        "UPDATE settlement_policies SET status = 'RETIRED', cutoff_hour = 1 WHERE id = :id",
        ids["settlement_policies"],
    )
    _expect_immutable(
        session,
        "UPDATE communication_records SET status = 'DELIVERED', rendered_body = 'x' WHERE id = :id",
        ids["communication_records"],
    )


@pytest.mark.parametrize("table", sorted(set(MUTABLE) - UNSEEDED_MUTABLE))
def test_mutable_workflow_tables_stay_unprotected(
    seeded: tuple[Session, dict[str, uuid.UUID]], table: str
) -> None:
    session, ids = seeded
    assert _triggers(session, table) == {}
    with session.begin_nested():
        result = session.execute(
            text(f"UPDATE {table} SET {MUTABLE[table]} WHERE id = :id"),
            {"id": ids[table]},
        )
    assert result.rowcount == 1


def test_every_table_from_migrations_0012_to_0017_is_classified(
    seeded: tuple[Session, dict[str, uuid.UUID]],
) -> None:
    session, _ids = seeded
    classified = {*ALL_PROTECTED, *MUTABLE, "request_logs", "usage_rollups"}
    assert len(classified) == 39
    with_trigger = {
        table
        for table in classified
        if any(
            function.startswith("relaypay_prevent_")
            for function in _triggers(session, table).values()
        )
    }
    assert with_trigger == set(ALL_PROTECTED)
