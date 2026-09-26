import hashlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.agent_runtime.models import WorkflowDefinition
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment, Organisation
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import Customer
from relaypay.subscriptions import execution
from relaypay.subscriptions.execution import (
    execute_message_action,
    execute_payment_action,
    run_recovery_action_batch,
)
from relaypay.subscriptions.intake import (
    RecurringPaymentFailedEnvelope,
    consume_recurring_payment_failed,
)
from relaypay.subscriptions.models import RecoveryCase, ScheduledRecoveryAction
from relaypay.subscriptions.network import (
    DeterministicCommunicationNetwork,
    DeterministicRecurringPaymentNetwork,
)
from relaypay.subscriptions.service import create_invoice, create_subscription, record_opt_out
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _envelope(
    *,
    organisation: Organisation,
    environment: Environment,
    subscription_id: str,
    invoice_id: str,
    provider_attempt_id: str,
    provider_code: str,
    outcome: str,
    occurred_at: datetime,
) -> RecurringPaymentFailedEnvelope:
    payload = {
        "subscriptionId": subscription_id,
        "invoiceId": invoice_id,
        "providerAttemptId": provider_attempt_id,
        "providerCode": provider_code,
        "outcome": outcome,
        "evidence": {"verified": outcome != "TRANSPORT_UNKNOWN", "source": "fixture"},
    }
    return RecurringPaymentFailedEnvelope.model_validate(
        {
            "eventId": new_public_id("bev"),
            "eventType": "recurring-payment.failed.v1",
            "schemaVersion": 1,
            "occurredAt": occurred_at,
            "organisationId": organisation.public_id,
            "environmentId": environment.public_id,
            "resourceType": "subscription_invoice",
            "resourceId": invoice_id,
            "payload": payload,
            "payloadSha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        }
    )


def test_recovery_enforces_ambiguity_consent_opt_out_and_terminal_stop() -> None:
    database_url = os.getenv(
        "RELAYPAY_DATABASE_URL",
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
    )
    engine = build_engine(database_url, application_name="m12-subscription-recovery-proof")
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    try:
        with factory() as session, session.begin():
            organisation = Organisation(
                id=new_uuid(),
                public_id=new_public_id("org"),
                name="Recovery proof",
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
            customer = Customer(
                id=new_uuid(),
                public_id=new_public_id("cus"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                merchant_customer_reference=f"recovery-{new_uuid().hex}",
                display_name="Synthetic Subscriber",
            )
            definition = WorkflowDefinition(
                id=new_uuid(),
                public_id=new_public_id("wdf"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                name="subscription-recovery",
                version=1,
                definition_sha256=hashlib.sha256(b"subscription-recovery-v1").digest(),
                definition={"steps": []},
                status="ACTIVE",
            )
            session.add_all([customer, definition])
            subscription = create_subscription(
                session,
                organisation_id=organisation.id,
                environment_id=environment.id,
                customer_public_id=customer.public_id,
                external_id=f"subscription-{new_uuid().hex}",
                plan_reference="monthly",
                amount=12_500,
                consent={"channels": ["EMAIL"], "displayName": "Synthetic Subscriber"},
            )
            session.flush([subscription])
            unknown_invoice = create_invoice(
                session,
                subscription=subscription,
                external_id=f"invoice-{new_uuid().hex}",
                due_at=now,
            )
            session.flush([unknown_invoice])
            unknown = consume_recurring_payment_failed(
                session,
                _envelope(
                    organisation=organisation,
                    environment=environment,
                    subscription_id=subscription.public_id,
                    invoice_id=unknown_invoice.public_id,
                    provider_attempt_id=f"provider-{new_uuid().hex}",
                    provider_code="TRANSPORT_UNKNOWN",
                    outcome="TRANSPORT_UNKNOWN",
                    occurred_at=now,
                ),
            )
            assert unknown.case is None

            opted_out_invoice = create_invoice(
                session,
                subscription=subscription,
                external_id=f"invoice-{new_uuid().hex}",
                due_at=now,
            )
            session.flush([opted_out_invoice])
            opted_out = consume_recurring_payment_failed(
                session,
                _envelope(
                    organisation=organisation,
                    environment=environment,
                    subscription_id=subscription.public_id,
                    invoice_id=opted_out_invoice.public_id,
                    provider_attempt_id=f"provider-{new_uuid().hex}",
                    provider_code="AUTHENTICATION_REQUIRED",
                    outcome="VERIFIED_FAILED",
                    occurred_at=now,
                ),
            )
            assert opted_out.case is not None
            opted_out_action = session.scalar(
                select(ScheduledRecoveryAction).where(
                    ScheduledRecoveryAction.recovery_case_id == opted_out.case.id
                )
            )
            assert opted_out_action is not None
            record_opt_out(
                session,
                case=opted_out.case,
                source_event_id=new_public_id("bev"),
                channel="ALL",
                received_at=now,
            )
            opted_out_action_id = opted_out_action.public_id

            recovered_invoice = create_invoice(
                session,
                subscription=subscription,
                external_id=f"invoice-{new_uuid().hex}",
                due_at=now,
            )
            session.flush([recovered_invoice])
            recovered = consume_recurring_payment_failed(
                session,
                _envelope(
                    organisation=organisation,
                    environment=environment,
                    subscription_id=subscription.public_id,
                    invoice_id=recovered_invoice.public_id,
                    provider_attempt_id=f"provider-{new_uuid().hex}",
                    provider_code="INSUFFICIENT_FUNDS",
                    outcome="VERIFIED_FAILED",
                    occurred_at=now,
                ),
            )
            assert recovered.case is not None
            actions = list(
                session.scalars(
                    select(ScheduledRecoveryAction)
                    .where(ScheduledRecoveryAction.recovery_case_id == recovered.case.id)
                    .order_by(ScheduledRecoveryAction.sequence)
                ).all()
            )
            assert sum(item.action_type == "PAYMENT_RETRY" for item in actions) == 3
            assert sum(item.action_type == "MESSAGE" for item in actions) == 3
            message_action_id = next(
                item.public_id for item in actions if item.action_type == "MESSAGE"
            )
            payment_action_id = next(
                item.public_id for item in actions if item.action_type == "PAYMENT_RETRY"
            )
            recovered_case_id = recovered.case.id

        blocked_network = DeterministicCommunicationNetwork()
        with pytest.raises(RelayPayError) as blocked:
            execute_message_action(
                factory,
                action_public_id=opted_out_action_id,
                network=blocked_network,
                now=now,
            )
        assert blocked.value.code == "RECOVERY_CASE_TERMINAL"
        assert blocked_network.effect_count == 0

        messages = DeterministicCommunicationNetwork(lose_first_response=True)
        first_message = execute_message_action(
            factory, action_public_id=message_action_id, network=messages, now=now
        )
        assert first_message.status == "AMBIGUOUS"
        recovered_message = execute_message_action(
            factory, action_public_id=message_action_id, network=messages, now=now
        )
        assert recovered_message.status == "EXECUTED"
        assert messages.effect_count == 1

        payments = DeterministicRecurringPaymentNetwork(outcome="SUCCEEDED")
        payment = execute_payment_action(
            factory,
            action_public_id=payment_action_id,
            network=payments,
            now=now + timedelta(hours=24),
        )
        assert payment.status == "EXECUTED"
        assert payments.effect_count == 1
        with factory() as session, session.begin():
            case = session.get(RecoveryCase, recovered_case_id)
            assert case is not None and case.status == "RECOVERED"
            remaining = session.scalars(
                select(ScheduledRecoveryAction).where(
                    ScheduledRecoveryAction.recovery_case_id == case.id,
                    ScheduledRecoveryAction.status == "SCHEDULED",
                )
            ).all()
            assert remaining == []
    finally:
        engine.dispose()


def test_recovery_batch_empty_query_uses_an_explicit_transaction() -> None:
    database_url = os.getenv(
        "RELAYPAY_DATABASE_URL",
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
    )
    engine = build_engine(database_url, application_name="m12-recovery-empty-batch-proof")
    try:
        assert (
            run_recovery_action_batch(
                build_session_factory(engine),
                communication_network=DeterministicCommunicationNetwork(),
                payment_network=DeterministicRecurringPaymentNetwork(),
                now=datetime(2000, 1, 1, tzinfo=UTC),
            )
            == 0
        )
    finally:
        engine.dispose()


def test_recovery_batch_survives_action_lost_to_a_concurrent_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A case terminated between the batch's committed pre-check and the
    execute transaction makes _prepare_action raise after rolling back; the
    batch must skip that action and keep processing instead of dying (which
    would stall every remaining due action until manual intervention)."""
    database_url = os.getenv(
        "RELAYPAY_DATABASE_URL",
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
    )
    engine = build_engine(database_url, application_name="m12-recovery-batch-resilience")
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    try:
        with factory() as session, session.begin():
            organisation = Organisation(
                id=new_uuid(),
                public_id=new_public_id("org"),
                name="Recovery batch resilience",
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
            customer = Customer(
                id=new_uuid(),
                public_id=new_public_id("cus"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                merchant_customer_reference=f"recovery-{new_uuid().hex}",
                display_name="Synthetic Subscriber",
            )
            definition = WorkflowDefinition(
                id=new_uuid(),
                public_id=new_public_id("wdf"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                name="subscription-recovery",
                version=1,
                definition_sha256=hashlib.sha256(b"subscription-recovery-v1").digest(),
                definition={"steps": []},
                status="ACTIVE",
            )
            session.add_all([customer, definition])
            subscription = create_subscription(
                session,
                organisation_id=organisation.id,
                environment_id=environment.id,
                customer_public_id=customer.public_id,
                external_id=f"subscription-{new_uuid().hex}",
                plan_reference="monthly",
                amount=12_500,
                consent={"channels": ["EMAIL"], "displayName": "Synthetic Subscriber"},
            )
            session.flush([subscription])
            invoice = create_invoice(
                session,
                subscription=subscription,
                external_id=f"invoice-{new_uuid().hex}",
                due_at=now,
            )
            session.flush([invoice])
            outcome = consume_recurring_payment_failed(
                session,
                _envelope(
                    organisation=organisation,
                    environment=environment,
                    subscription_id=subscription.public_id,
                    invoice_id=invoice.public_id,
                    provider_attempt_id=f"provider-{new_uuid().hex}",
                    provider_code="INSUFFICIENT_FUNDS",
                    outcome="VERIFIED_FAILED",
                    occurred_at=now,
                ),
            )
            assert outcome.case is not None

        real_message = execution.execute_message_action
        real_payment = execution.execute_payment_action
        attempts = {"count": 0}

        def make_flaky(real: object) -> object:
            def flaky(*args: object, **kwargs: object) -> object:
                # Whichever action type the batch selects first simulates a
                # case that a concurrent writer terminated mid-flight.
                if attempts["count"] == 0:
                    attempts["count"] += 1
                    raise RelayPayError(
                        code="RECOVERY_CASE_TERMINAL",
                        message="simulated concurrent termination",
                        http_status=409,
                    )
                return real(*args, **kwargs)

            return flaky

        monkeypatch.setattr(execution, "execute_message_action", make_flaky(real_message))
        monkeypatch.setattr(execution, "execute_payment_action", make_flaky(real_payment))
        processed = run_recovery_action_batch(
            factory,
            communication_network=DeterministicCommunicationNetwork(),
            payment_network=DeterministicRecurringPaymentNetwork(),
            # Seven days: inside the 14-day policy window but past the first
            # scheduled action windows, so the batch has real work to do.
            now=now + timedelta(days=7),
            limit=10,
        )
        # Pre-fix, the simulated termination raised out of the batch and
        # killed it. Now the skip consumes a slot, the action is not retried
        # through the raising path more than once, and the batch returns.
        assert processed >= 1
        assert attempts["count"] == 1
    finally:
        engine.dispose()
