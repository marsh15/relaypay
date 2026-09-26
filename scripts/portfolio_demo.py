"""One runnable synthetic journey across the integrated RelayPay platform.

Payment authorization and capture through the deterministic provider compose
service, a settlement intelligence question, a subscription recovery case, a
dispute response draft against the captured payment, a merchant risk review,
and the cross-workflow portfolio analytics summary. Requires the local compose
stack (Postgres on 55432, provider on 8001). All data is synthetic and unique
per run.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from relaypay.config import Settings, get_settings
from relaypay.contracts import EmptyCommand, PaymentIntentCreate
from relaypay.database import build_engine, build_session_factory
from relaypay.disputes.evidence import draft_from_allowlisted_evidence
from relaypay.disputes.service import create_draft, open_case
from relaypay.idempotency import Fingerprint, build_fingerprint
from relaypay.identity.models import Environment, Organisation
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
from relaypay.merchant_balances.service import ensure_default_merchant_account
from relaypay.observability.metrics import operations_metrics
from relaypay.payments.models import Customer
from relaypay.payments.service import (
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
)
from relaypay.provider_operations.service import HTTPProviderTransport, dispatch_operation
from relaypay.settlement_intelligence.provider import SettlementFakeProvider
from relaypay.settlement_intelligence.service import answer_question
from relaypay.subscriptions.models import RecurringPaymentAttempt, ScheduledRecoveryAction
from relaypay.subscriptions.service import (
    create_invoice,
    create_subscription,
    ensure_default_policy,
    open_recovery_case,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from scripts.run_evaluations import FixedFindingsProvider

QUESTION = "Why is today's settlement lower than the forecast?"


def _command_fingerprint(payment_public_id: str, action: str) -> Fingerprint:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template=f"/payment_intents/{{payment_intent_id}}/{action}",
        path_params={"payment_intent_id": payment_public_id},
        body=EmptyCommand(),
    )


def _send_operation(
    factory: sessionmaker[Session],
    settings: Settings,
    organisation_id: uuid.UUID,
    operation_public_id: str,
) -> None:
    dispatch_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_public_id,
        provider_account_id=settings.PROVIDER_ACCOUNT_ID,
        provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
        transport=HTTPProviderTransport(base_url=settings.PROVIDER_BASE_URL, timeout_seconds=5.0),
    )


def _run(factory: sessionmaker[Session], settings: Settings) -> dict[str, object]:
    now = datetime.now(UTC)
    pepper = settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value()

    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"),
            name=f"Portfolio demo {uuid.uuid4().hex[:8]}",
            status="ACTIVE",
        )
        session.add(organisation)
        session.flush([organisation])
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation.id,
            merchant_customer_reference=f"demo-customer-{uuid.uuid4().hex}",
        )
        session.add(customer)
        session.add_all(
            [
                LedgerAccount(
                    organisation_id=organisation.id,
                    code="PROVIDER_CLEARING_ASSET",
                    name="Provider clearing",
                    account_type="ASSET",
                    currency="INR",
                ),
                LedgerAccount(
                    organisation_id=organisation.id,
                    code="MERCHANT_PAYABLE_LIABILITY",
                    name="Merchant payable",
                    account_type="LIABILITY",
                    currency="INR",
                ),
            ]
        )
        session.flush([customer])
        environment = session.scalar(
            select(Environment).where(Environment.organisation_id == organisation.id)
        )
        assert environment is not None
        org_id, env_id = organisation.id, environment.id
        org_public, env_public = organisation.public_id, environment.public_id

    # --- Payment: intent -> authorization -> capture via the provider service.
    payload = PaymentIntentCreate(
        customer_id=customer.public_id,
        merchant_reference=f"demo-order-{uuid.uuid4().hex}",
        amount=250_000,
        currency="INR",
    )
    payment = create_payment_intent(
        factory,
        organisation_id=org_id,
        payload=payload,
        idempotency_key=f"demo-payment-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents",
            path_params={},
            body=payload,
        ),
        key_pepper=pepper,
    )
    payment_public_id = str(json.loads(payment.body)["id"])
    authorize_result = initiate_authorization(
        factory,
        organisation_id=org_id,
        payment_public_id=payment_public_id,
        idempotency_key=f"demo-authorize-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_public_id, "authorize"),
        key_pepper=pepper,
    )
    _send_operation(
        factory, settings, org_id, str(json.loads(authorize_result.body)["operationId"])
    )
    capture = initiate_capture(
        factory,
        organisation_id=org_id,
        payment_public_id=payment_public_id,
        idempotency_key=f"demo-capture-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_public_id, "capture"),
        key_pepper=pepper,
    )
    _send_operation(factory, settings, org_id, str(json.loads(capture.body)["operationId"]))

    # --- Settlement intelligence question against the merchant account.
    with factory() as session, session.begin():
        account = ensure_default_merchant_account(
            session, organisation_id=org_id, environment_id=env_id
        )
    settlement_payload, _replayed = answer_question(
        factory,
        organisation_id=org_id,
        environment_id=env_id,
        merchant_account_id=account.id,
        organisation_public_id=org_public,
        environment_public_id=env_public,
        question_text=QUESTION,
        idempotency_key_digest=hashlib.sha256(uuid.uuid4().bytes).digest(),
        provider=SettlementFakeProvider(),
        now=now,
    )

    # --- Subscription recovery case on a failed renewal attempt.
    with factory() as session, session.begin():
        policy = ensure_default_policy(session, organisation_id=org_id, environment_id=env_id)
        subscriber = Customer(
            public_id=new_public_id("cus"),
            organisation_id=org_id,
            environment_id=env_id,
            merchant_customer_reference=f"demo-subscriber-{uuid.uuid4().hex}",
        )
        session.add(subscriber)
        session.flush([subscriber])
        subscription = create_subscription(
            session,
            organisation_id=org_id,
            environment_id=env_id,
            customer_public_id=subscriber.public_id,
            external_id=f"demo-sub-{uuid.uuid4().hex}",
            plan_reference="demo-plan",
            amount=99_000,
            consent={"channels": ["EMAIL"], "displayName": "Demo Subscriber"},
        )
        session.flush([subscription])
        invoice = create_invoice(
            session,
            subscription=subscription,
            external_id=f"{subscription.public_id}-inv",
            due_at=now,
        )
        session.flush([invoice])
        from relaypay.agent_runtime.models import WorkflowDefinition, WorkflowRun

        definition = WorkflowDefinition(
            id=uuid.uuid4(),
            public_id=new_public_id("wdf"),
            organisation_id=org_id,
            environment_id=env_id,
            name="demo-recovery",
            version=1,
            definition_sha256=hashlib.sha256(b"demo-recovery").digest(),
            definition={"steps": [{"key": "recover", "kind": "SYSTEM"}]},
            status="ACTIVE",
        )
        session.add(definition)
        session.flush([definition])
        run = WorkflowRun(
            id=uuid.uuid4(),
            public_id=new_public_id("wfr"),
            organisation_id=org_id,
            environment_id=env_id,
            workflow_definition_id=definition.id,
            route="DEMO:recovery",
            idempotency_digest=hashlib.sha256(uuid.uuid4().bytes).digest(),
            status="RUNNING",
            token_budget=10_000,
            cost_budget_usd_micros=100_000,
            tokens_used=0,
            cost_used_usd_micros=0,
        )
        session.add(run)
        session.flush([run])
        attempt = RecurringPaymentAttempt(
            id=uuid.uuid4(),
            public_id=new_public_id("rpa"),
            organisation_id=org_id,
            environment_id=env_id,
            invoice_id=invoice.id,
            attempt_number=1,
            provider_attempt_id=f"demo-attempt-{uuid.uuid4().hex}",
            outcome="VERIFIED_FAILED",
            failure_classification="RETRYABLE_SOFT_DECLINE",
            provider_code="INSUFFICIENT_FUNDS",
            evidence={"verified": True, "source": "demo"},
            evidence_sha256=hashlib.sha256(b"demo").digest(),
            occurred_at=now,
        )
        session.add(attempt)
        session.flush([attempt])
        recovery_case = open_recovery_case(
            session,
            subscription=subscription,
            invoice=invoice,
            trigger_attempt=attempt,
            workflow_run=run,
            policy=policy,
            now=now,
        )
        recovery_case_public_id = recovery_case.public_id
        scheduled_actions = len(
            session.scalars(
                select(ScheduledRecoveryAction).where(
                    ScheduledRecoveryAction.recovery_case_id == recovery_case.id
                )
            ).all()
        )

    # --- Dispute response draft from the captured payment's allowlisted evidence.
    with factory() as session, session.begin():
        from relaypay.agent_runtime.models import WorkflowDefinition, WorkflowRun

        dispute_definition = WorkflowDefinition(
            id=uuid.uuid4(),
            public_id=new_public_id("wdf"),
            organisation_id=org_id,
            environment_id=env_id,
            name="dispute-response-demo",
            version=1,
            definition_sha256=hashlib.sha256(b"dispute-demo").digest(),
            definition={"steps": [{"key": "draft", "kind": "AGENT"}]},
            status="ACTIVE",
        )
        session.add(dispute_definition)
        session.flush([dispute_definition])
        dispute_run = WorkflowRun(
            id=uuid.uuid4(),
            public_id=new_public_id("wfr"),
            organisation_id=org_id,
            environment_id=env_id,
            workflow_definition_id=dispute_definition.id,
            route="EVENT:dispute.created.v1",
            idempotency_digest=hashlib.sha256(uuid.uuid4().bytes).digest(),
            status="RUNNING",
            token_budget=10_000,
            cost_budget_usd_micros=100_000,
            tokens_used=0,
            cost_used_usd_micros=0,
        )
        session.add(dispute_run)
        session.flush([dispute_run])
        dispute = open_case(
            session,
            organisation_id=org_id,
            environment_id=env_id,
            payment_public_id=payment_public_id,
            workflow_run_id=dispute_run.id,
            network_dispute_id=f"demo-dp-{uuid.uuid4().hex}",
            reason_code="PRODUCT_NOT_RECEIVED",
            amount=250_000,
            due_at=now + timedelta(days=7),
            source_snapshot={
                "paymentId": payment_public_id,
                "deliveryStatus": "DELIVERED",
                "deliveryProof": "demo-pod-001",
            },
        )
        session.flush([dispute])
        draft = create_draft(
            session,
            case=dispute,
            draft=draft_from_allowlisted_evidence(dispute),
            author_type="AGENT",
            author_user_id=None,
        )
        dispute_public_id, draft_public_id = dispute.public_id, draft.public_id

    # --- Merchant risk review on the deterministic site snapshot.
    from relaypay.risk_review.service import execute_review, prepare_review
    from relaypay.risk_review.snapshot import DeterministicSiteSnapshotSource

    prepared = prepare_review(
        factory,
        organisation_id=org_id,
        environment_id=env_id,
        organisation_public_id=org_public,
        environment_public_id=env_public,
        site_ref="COMPLETE_CLEAN",
        source=DeterministicSiteSnapshotSource(),
        source_url="deterministic://portfolio-demo",
    )
    review = execute_review(
        factory, prepared, provider=FixedFindingsProvider(), now=datetime.now(UTC)
    )

    # --- Cross-workflow portfolio analytics.
    from relaypay.analytics.service import portfolio_analytics, refresh_portfolio_metrics

    with factory() as session, session.begin():
        from relaypay.payments.models import Capture, PaymentIntent

        refresh_portfolio_metrics(
            session,
            organisation_id=org_id,
            environment_id=env_id,
            metrics=operations_metrics(),
        )
        analytics = portfolio_analytics(
            session, organisation_id=org_id, environment_id=env_id
        ).payload()
        capture_status = session.scalar(
            select(Capture.status)
            .join(PaymentIntent, PaymentIntent.id == Capture.payment_intent_id)
            .where(PaymentIntent.public_id == payment_public_id)
        )

    return {
        "paymentId": payment_public_id,
        "captureStatus": capture_status,
        "settlementIntent": settlement_payload["intent"],
        "recoveryCaseId": recovery_case_public_id,
        "recoveryScheduledActions": scheduled_actions,
        "disputeId": dispute_public_id,
        "disputeDraftId": draft_public_id,
        "riskReviewId": review["id"],
        "riskReviewStatus": review["status"],
        "riskTotalScore": (
            versions[-1].get("totalScore")
            if isinstance(versions := review.get("versions"), list) and versions
            else None
        ),
        "portfolioAnalytics": analytics,
        "syntheticDataOnly": True,
    }


def main() -> None:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-portfolio-demo",
    )
    factory = build_session_factory(engine)
    try:
        print(json.dumps(_run(factory, settings), indent=2, sort_keys=True, default=str))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
