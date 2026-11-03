import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from relaypay.agent_runtime.events import record_consumption
from relaypay.agent_runtime.models import WorkflowDefinition
from relaypay.config import Settings
from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment, Organisation, OrganisationMembership, User
from relaypay.identity.security import hash_password
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import Customer
from relaypay.subscriptions.intake import (
    RecurringPaymentFailedEnvelope,
    consume_recurring_payment_failed,
)
from relaypay.subscriptions.models import RecurringPaymentAttempt, Subscription, SubscriptionInvoice
from relaypay.subscriptions.service import (
    create_invoice,
    create_subscription,
    record_payment_attempt,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app
from tests.support.db import provider_database_url, relaypay_database_url, relaypay_factory
from tests.support.fake_provider import ACCOUNT_ID, SIGNING_SECRET, FakeProvider
from tests.support.world import _api_key_pepper

pytestmark = pytest.mark.integration

CONSUMER = "subscription-recovery-agent-v1"


@dataclass(frozen=True, slots=True)
class _Tenant:
    organisation_id: uuid.UUID
    organisation_public_id: str
    environment_id: uuid.UUID
    environment_public_id: str
    subscription_id: uuid.UUID
    subscription_public_id: str


def _create_tenant(factory: sessionmaker[Session]) -> _Tenant:
    with factory() as session, session.begin():
        organisation = Organisation(
            id=new_uuid(),
            public_id=new_public_id("org"),
            name="Replay identity",
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
            merchant_customer_reference=f"replay-{new_uuid().hex}",
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
        return _Tenant(
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=environment.id,
            environment_public_id=environment.public_id,
            subscription_id=subscription.id,
            subscription_public_id=subscription.public_id,
        )


def _create_invoice(factory: sessionmaker[Session], tenant: _Tenant) -> str:
    with factory() as session, session.begin():
        subscription = session.get(Subscription, tenant.subscription_id)
        assert subscription is not None
        invoice = create_invoice(
            session,
            subscription=subscription,
            external_id=f"invoice-{new_uuid().hex}",
            due_at=datetime.now(UTC),
        )
        session.flush([invoice])
        return invoice.public_id


def _envelope(
    tenant: _Tenant,
    *,
    invoice_public_id: str,
    provider_attempt_id: str,
    event_id: str | None = None,
    evidence: dict[str, object] | None = None,
) -> RecurringPaymentFailedEnvelope:
    payload = {
        "subscriptionId": tenant.subscription_public_id,
        "invoiceId": invoice_public_id,
        "providerAttemptId": provider_attempt_id,
        "providerCode": "INSUFFICIENT_FUNDS",
        "outcome": "VERIFIED_FAILED",
        "evidence": evidence or {"verified": True, "source": "fixture"},
    }
    return RecurringPaymentFailedEnvelope.model_validate(
        {
            "eventId": event_id or new_public_id("bev"),
            "eventType": "recurring-payment.failed.v1",
            "schemaVersion": 1,
            "occurredAt": datetime.now(UTC),
            "organisationId": tenant.organisation_public_id,
            "environmentId": tenant.environment_public_id,
            "resourceType": "subscription_invoice",
            "resourceId": invoice_public_id,
            "payload": payload,
            "payloadSha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        }
    )


def _consume(
    factory: sessionmaker[Session], envelope: RecurringPaymentFailedEnvelope
) -> tuple[str, str | None]:
    with factory() as session, session.begin():
        result = consume_recurring_payment_failed(session, envelope)
        case_id = None if result.case is None else result.case.public_id
        return result.attempt.public_id, case_id


def _attempt_count(factory: sessionmaker[Session], provider_attempt_id: str) -> int:
    with factory() as session, session.begin():
        return len(
            session.scalars(
                select(RecurringPaymentAttempt.id).where(
                    RecurringPaymentAttempt.provider_attempt_id == provider_attempt_id
                )
            ).all()
        )


def test_identical_redelivery_returns_the_original_attempt_and_case() -> None:
    with relaypay_factory("recurring-intake-replay-tests") as factory:
        tenant = _create_tenant(factory)
        invoice_id = _create_invoice(factory, tenant)
        attempt_id = f"provider-{new_uuid().hex}"
        envelope = _envelope(tenant, invoice_public_id=invoice_id, provider_attempt_id=attempt_id)

        first = _consume(factory, envelope)
        replayed = _consume(factory, envelope)

        assert first[1] is not None
        assert replayed == first
        assert _attempt_count(factory, attempt_id) == 1


def test_changed_evidence_on_a_consumed_event_id_is_rejected() -> None:
    with relaypay_factory("recurring-intake-replay-tests") as factory:
        tenant = _create_tenant(factory)
        invoice_id = _create_invoice(factory, tenant)
        attempt_id = f"provider-{new_uuid().hex}"
        event_id = new_public_id("bev")
        original = _envelope(
            tenant,
            invoice_public_id=invoice_id,
            provider_attempt_id=attempt_id,
            event_id=event_id,
        )
        first = _consume(factory, original)

        changed = _envelope(
            tenant,
            invoice_public_id=invoice_id,
            provider_attempt_id=attempt_id,
            event_id=event_id,
            evidence={"verified": True, "source": "changed"},
        )
        with pytest.raises(RelayPayError) as rejected:
            _consume(factory, changed)

        assert rejected.value.code == "EVENT_REPLAY_CONFLICT"
        assert rejected.value.http_status == 409
        assert attempt_id not in rejected.value.message
        assert _attempt_count(factory, attempt_id) == 1
        assert _consume(factory, original) == first


def test_changed_provider_attempt_on_a_consumed_event_id_is_rejected() -> None:
    with relaypay_factory("recurring-intake-replay-tests") as factory:
        tenant = _create_tenant(factory)
        invoice_id = _create_invoice(factory, tenant)
        attempt_id = f"provider-{new_uuid().hex}"
        other_attempt_id = f"provider-{new_uuid().hex}"
        event_id = new_public_id("bev")
        _consume(
            factory,
            _envelope(
                tenant,
                invoice_public_id=invoice_id,
                provider_attempt_id=attempt_id,
                event_id=event_id,
            ),
        )

        with pytest.raises(RelayPayError) as rejected:
            _consume(
                factory,
                _envelope(
                    tenant,
                    invoice_public_id=invoice_id,
                    provider_attempt_id=other_attempt_id,
                    event_id=event_id,
                ),
            )

        assert rejected.value.code == "EVENT_REPLAY_CONFLICT"
        assert _attempt_count(factory, other_attempt_id) == 0
        assert _attempt_count(factory, attempt_id) == 1


def test_consumed_event_id_cannot_be_replayed_from_another_tenant() -> None:
    with relaypay_factory("recurring-intake-replay-tests") as factory:
        owner = _create_tenant(factory)
        intruder = _create_tenant(factory)
        owner_invoice_id = _create_invoice(factory, owner)
        intruder_invoice_id = _create_invoice(factory, intruder)
        attempt_id = f"provider-{new_uuid().hex}"
        event_id = new_public_id("bev")
        owned = _envelope(
            owner,
            invoice_public_id=owner_invoice_id,
            provider_attempt_id=attempt_id,
            event_id=event_id,
        )
        owner_result = _consume(factory, owned)

        spoofed = _envelope(
            intruder,
            invoice_public_id=intruder_invoice_id,
            provider_attempt_id=attempt_id,
            event_id=event_id,
        )
        with pytest.raises(RelayPayError) as rejected:
            _consume(factory, spoofed)

        assert rejected.value.code == "EVENT_REPLAY_CONFLICT"
        assert owner.organisation_public_id not in rejected.value.message
        assert attempt_id not in rejected.value.message
        assert _consume(factory, owned) == owner_result
        assert _attempt_count(factory, attempt_id) == 1


def test_fresh_event_cannot_adopt_an_attempt_owned_by_another_invoice() -> None:
    with relaypay_factory("recurring-intake-replay-tests") as factory:
        owner = _create_tenant(factory)
        intruder = _create_tenant(factory)
        owner_invoice_id = _create_invoice(factory, owner)
        owner_second_invoice_id = _create_invoice(factory, owner)
        intruder_invoice_id = _create_invoice(factory, intruder)
        attempt_id = f"provider-{new_uuid().hex}"
        owned = _envelope(owner, invoice_public_id=owner_invoice_id, provider_attempt_id=attempt_id)
        owner_result = _consume(factory, owned)

        for tenant, invoice_id in (
            (intruder, intruder_invoice_id),
            (owner, owner_second_invoice_id),
        ):
            with pytest.raises(RelayPayError) as rejected:
                _consume(
                    factory,
                    _envelope(tenant, invoice_public_id=invoice_id, provider_attempt_id=attempt_id),
                )
            assert rejected.value.code == "RECURRING_ATTEMPT_CONFLICT"
            assert owner.organisation_public_id not in rejected.value.message
            assert attempt_id not in rejected.value.message

        assert _attempt_count(factory, attempt_id) == 1
        assert _consume(factory, owned) == owner_result


def test_identical_replay_without_an_owned_attempt_is_a_conflict() -> None:
    with relaypay_factory("recurring-intake-replay-tests") as factory:
        owner = _create_tenant(factory)
        intruder = _create_tenant(factory)
        owner_invoice_id = _create_invoice(factory, owner)
        intruder_invoice_id = _create_invoice(factory, intruder)
        attempt_id = f"provider-{new_uuid().hex}"
        _consume(
            factory,
            _envelope(owner, invoice_public_id=owner_invoice_id, provider_attempt_id=attempt_id),
        )
        envelope = _envelope(
            intruder,
            invoice_public_id=intruder_invoice_id,
            provider_attempt_id=attempt_id,
        )
        # A marker for this scope exists but no recurring attempt was recorded for it.
        with factory() as session, session.begin():
            assert record_consumption(
                session,
                organisation_id=intruder.organisation_id,
                environment_id=intruder.environment_id,
                consumer_name=CONSUMER,
                event_id=envelope.event_id,
                payload_sha256=bytes.fromhex(envelope.payload_sha256),
            )

        with pytest.raises(RelayPayError) as rejected:
            _consume(factory, envelope)

        assert rejected.value.code == "EVENT_REPLAY_CONFLICT"
        assert rejected.value.http_status == 409
        assert attempt_id not in rejected.value.message
        assert _attempt_count(factory, attempt_id) == 1


def test_unrelated_integrity_failure_is_not_treated_as_a_replay() -> None:
    with (
        relaypay_factory("recurring-intake-replay-tests") as factory,
        factory() as session,
        session.begin(),
        pytest.raises(IntegrityError),
    ):
        record_consumption(
            session,
            organisation_id=new_uuid(),
            environment_id=new_uuid(),
            consumer_name="replay-identity-tests",
            event_id=new_public_id("bev"),
            payload_sha256=hashlib.sha256(b"payload").digest(),
        )


def test_service_binds_attempt_to_invoice_scope_and_semantics() -> None:
    with relaypay_factory("recurring-service-identity-tests") as factory:
        owner = _create_tenant(factory)
        other = _create_tenant(factory)
        invoice_id = _create_invoice(factory, owner)
        other_ids = (_create_invoice(factory, owner), _create_invoice(factory, other))
        attempt_id = f"provider-{new_uuid().hex}"
        with factory() as session, session.begin():
            invoice = session.scalar(
                select(SubscriptionInvoice).where(SubscriptionInvoice.public_id == invoice_id)
            )
            assert invoice is not None
            args = {
                "provider_attempt_id": attempt_id,
                "provider_code": "INSUFFICIENT_FUNDS",
                "outcome": "VERIFIED_FAILED",
                "occurred_at": datetime.now(UTC),
                "evidence": {"verified": True},
            }
            original = record_payment_attempt(session, invoice=invoice, **args)
            session.flush()
            assert record_payment_attempt(session, invoice=invoice, **args).id == original.id
            for changes in (
                {"provider_code": "OTHER"},
                {"outcome": "TRANSPORT_UNKNOWN"},
                {"evidence": {"verified": False}},
            ):
                with pytest.raises(RelayPayError) as rejected:
                    record_payment_attempt(session, invoice=invoice, **(args | changes))
                assert rejected.value.code == "RECURRING_ATTEMPT_CONFLICT"
                assert attempt_id not in rejected.value.message
            for other_id in other_ids:
                foreign = session.scalar(
                    select(SubscriptionInvoice).where(SubscriptionInvoice.public_id == other_id)
                )
                assert foreign is not None
                with pytest.raises(RelayPayError) as rejected:
                    record_payment_attempt(session, invoice=foreign, **args)
                assert rejected.value.code == "RECURRING_ATTEMPT_CONFLICT"
                assert rejected.value.http_status == 409
                assert other_id not in rejected.value.message


def test_recurring_failure_http_replay_conflicts_and_foreign_ids() -> None:
    settings = Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL=relaypay_database_url(),
        PROVIDER_DATABASE_URL=provider_database_url(),
        RECEIVER_DATABASE_URL=relaypay_database_url(),
        SESSION_SECRET="recurring-session-secret-at-least-32-bytes-long",
        CSRF_SECRET="recurring-csrf-secret-at-least-32-bytes-long",
        API_KEY_PEPPER=_api_key_pepper(),
        IDEMPOTENCY_KEY_PEPPER="recurring-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-recurring-tests",
        PROVIDER_ACCOUNT_ID=ACCOUNT_ID,
        PROVIDER_SIGNING_SECRET=SIGNING_SECRET,
        PROVIDER_CONTROL_SECRET="recurring-provider-control",
        RECEIVER_WEBHOOK_SECRET="recurring-receiver-webhook",
    )
    with relaypay_factory("recurring-http-tests") as factory:
        owner, other = _create_tenant(factory), _create_tenant(factory)
        invoice_id = _create_invoice(factory, owner)
        foreign_invoice = _create_invoice(factory, other)
        foreign_attempt = f"provider-{new_uuid().hex}"
        _consume(
            factory,
            _envelope(
                other, invoice_public_id=foreign_invoice, provider_attempt_id=foreign_attempt
            ),
        )
        email = f"recurring-{new_uuid().hex}@example.test"
        password = "Synthetic-Password-123!"
        with factory() as session, session.begin():
            user = User(
                email_normalized=email,
                display_name="Synthetic admin",
                password_hash=hash_password(password),
                status="ACTIVE",
            )
            session.add(user)
            session.flush([user])
            session.add(
                OrganisationMembership(
                    organisation_id=owner.organisation_id,
                    user_id=user.id,
                    role="ORGANISATION_ADMIN",
                    status="ACTIVE",
                )
            )
        with TestClient(create_app(settings, provider_transport=FakeProvider())) as client:
            login = client.post("/api/session/login", json={"email": email, "password": password})
            assert login.status_code == 200
            headers = {"X-CSRF-Token": login.json()["csrfToken"], "Idempotency-Key": "same-key"}
            base = f"/api/admin/v1/environments/{owner.environment_public_id}/subscription-invoices"
            body = {
                "subscriptionId": owner.subscription_public_id,
                "providerAttemptId": f"provider-{new_uuid().hex}",
                "providerCode": "INSUFFICIENT_FUNDS",
                "outcome": "VERIFIED_FAILED",
                "evidence": {"verified": True},
            }
            first = client.post(f"{base}/{invoice_id}/failures", json=body, headers=headers)
            assert first.status_code == 202, first.text
            replay = client.post(f"{base}/{invoice_id}/failures", json=body, headers=headers)
            assert replay.status_code == 202
            assert replay.json() == first.json()
            conflict = client.post(
                f"{base}/{invoice_id}/failures",
                json=body | {"evidence": {"verified": False}},
                headers=headers,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "EVENT_REPLAY_CONFLICT"
            foreign = client.post(f"{base}/{foreign_invoice}/failures", json=body, headers=headers)
            assert foreign.status_code == 404
            foreign_sub = client.post(
                f"{base}/{invoice_id}/failures",
                json=body | {"subscriptionId": other.subscription_public_id},
                headers=headers,
            )
            assert foreign_sub.status_code == 404
            foreign_attempt_response = client.post(
                f"{base}/{invoice_id}/failures",
                json=body | {"providerAttemptId": foreign_attempt},
                headers=headers | {"Idempotency-Key": "foreign-attempt"},
            )
            assert foreign_attempt_response.status_code == 409
            assert foreign_attempt_response.json()["error"]["code"] == "RECURRING_ATTEMPT_CONFLICT"
            assert foreign_attempt not in foreign_attempt_response.text


def test_dispute_consumer_marker_replays_and_rejects_changed_payload() -> None:
    with relaypay_factory("dispute-marker-replay-tests") as factory:
        tenant = _create_tenant(factory)
        event_id = new_public_id("bev")
        digest = hashlib.sha256(b"synthetic-dispute").digest()
        with factory() as session, session.begin():
            args = dict(
                organisation_id=tenant.organisation_id,
                environment_id=tenant.environment_id,
                consumer_name="dispute-response-agent-v1",
                event_id=event_id,
                payload_sha256=digest,
            )
            assert record_consumption(session, **args)
        with factory() as session, session.begin():
            assert not record_consumption(session, **args)
            with pytest.raises(RelayPayError) as rejected:
                record_consumption(session, **(args | {"payload_sha256": bytes(32)}))
            assert rejected.value.code == "EVENT_REPLAY_CONFLICT"
