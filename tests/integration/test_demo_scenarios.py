import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.crypto import encrypt_webhook_secret
from relaypay.event_delivery.delivery import DeliveryResponse
from relaypay.event_delivery.models import WebhookEndpoint, WebhookEndpointVersion
from relaypay.identity.models import Organisation, OrganisationMembership, User
from relaypay.identity.security import hash_password
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
from relaypay.mock_provider.models import ProviderAccount, ProviderEffect
from relaypay.mock_provider.service import (
    EffectCommand,
    apply_effect,
    configure_fault,
    lookup_effect,
)
from relaypay.provider_operations.service import ProviderTransport
from relaypay.provider_operations.service_types import ProviderObservation
from relaypay.receiver.service import receive_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app

pytestmark = pytest.mark.integration

RELAYPAY_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
PROVIDER_URL = "postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider"
RECEIVER_URL = "postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay"
SIGNING_SECRET = "scenario-provider-signing-secret"
CONTROL_SECRET = "scenario-provider-control-secret"
WEBHOOK_SECRET = "scenario-webhook-secret"
ENCRYPTION_KEY = "scenario-encryption-key"


class LocalProvider(ProviderTransport):
    def __init__(self, factory: sessionmaker[Session], account_id: str) -> None:
        self.factory = factory
        self.account_id = account_id

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        payload = json.loads(request_bytes)
        reply = apply_effect(
            self.factory,
            command=EffectCommand(
                account_id=payload["accountId"],
                stable_key=payload["stableKey"],
                operation_kind=payload["operationKind"],
                reference=payload["reference"],
                amount=payload["amount"],
                currency=payload["currency"],
            ),
            signing_secret=SIGNING_SECRET,
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        reply = lookup_effect(
            self.factory,
            account_public_id=account_id,
            stable_key=stable_key,
            signing_secret=SIGNING_SECRET,
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)

    def lose_next_response(self, stable_key: str) -> None:
        configure_fault(
            self.factory,
            account_public_id=self.account_id,
            stable_key=stable_key,
            fault_type="LOSE_RESPONSE",
        )

    def effect_count(self, stable_key: str) -> int:
        with self.factory() as session, session.begin():
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ProviderEffect)
                    .where(ProviderEffect.stable_key == stable_key)
                )
                or 0
            )


class LocalReceiver:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def send(self, *, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResponse:
        assert url == "http://receiver:8002/webhooks/relaypay"
        with self.factory() as session, session.begin():
            receive_event(
                session,
                body=body,
                event_id=headers["X-RelayPay-Event-Id"],
                timestamp_text=headers["X-RelayPay-Timestamp"],
                signature=headers["X-RelayPay-Signature"],
                secret=WEBHOOK_SECRET,
            )
        return DeliveryResponse(200)


@pytest.fixture
def scenario_client() -> Iterator[tuple[TestClient, str, str]]:
    relay_engine = build_engine(RELAYPAY_URL, application_name="scenario-api-seed")
    provider_engine = build_engine(PROVIDER_URL, application_name="scenario-provider-seed")
    receiver_engine = build_engine(RECEIVER_URL, application_name="scenario-receiver-seed")
    relay = build_session_factory(relay_engine)
    provider = build_session_factory(provider_engine)
    receiver = build_session_factory(receiver_engine)
    account_id = f"acct_scenario_{uuid.uuid4().hex}"
    email = f"scenario-{uuid.uuid4().hex}@example.test"
    password = "Synthetic-Scenario-Password!"
    with provider() as session, session.begin():
        session.add(
            ProviderAccount(
                public_id=account_id,
                name="Scenario provider",
                signing_secret_digest=hashlib.sha256(SIGNING_SECRET.encode()).digest(),
            )
        )
    with relay() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="Scenario tenant", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        user = User(
            email_normalized=email,
            display_name="Scenario administrator",
            password_hash=hash_password(password),
            platform_role="STANDARD",
            status="ACTIVE",
        )
        session.add(user)
        session.flush()
        session.add(
            OrganisationMembership(
                organisation_id=organisation.id,
                user_id=user.id,
                role="ORGANISATION_ADMIN",
                status="ACTIVE",
            )
        )
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
        endpoint = WebhookEndpoint(
            public_id=new_public_id("wh"),
            organisation_id=organisation.id,
            name="Bundled receiver",
            status="ACTIVE",
        )
        session.add(endpoint)
        session.flush()
        session.add(
            WebhookEndpointVersion(
                public_id=new_public_id("whv"),
                organisation_id=organisation.id,
                webhook_endpoint_id=endpoint.id,
                version=1,
                url="http://receiver:8002/webhooks/relaypay",
                encrypted_secret=encrypt_webhook_secret(WEBHOOK_SECRET, ENCRYPTION_KEY),
                subscribed_event_types=["payment.captured.v1"],
                active_from=datetime.now(UTC),
            )
        )
    settings = Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL=RELAYPAY_URL,
        PROVIDER_DATABASE_URL=PROVIDER_URL,
        RECEIVER_DATABASE_URL=RECEIVER_URL,
        SESSION_SECRET="scenario-session-secret-at-least-32-bytes",
        CSRF_SECRET="scenario-csrf-secret-at-least-32-bytes",
        API_KEY_PEPPER="scenario-api-pepper-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="scenario-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY=ENCRYPTION_KEY,
        PROVIDER_ACCOUNT_ID=account_id,
        PROVIDER_SIGNING_SECRET=SIGNING_SECRET,
        PROVIDER_CONTROL_SECRET=CONTROL_SECRET,
        RECEIVER_BASE_URL="http://receiver:8002",
        RECEIVER_WEBHOOK_SECRET=WEBHOOK_SECRET,
    )
    local_provider = LocalProvider(provider, account_id)
    with TestClient(
        create_app(
            settings,
            provider_transport=local_provider,
            scenario_fault_controller=local_provider,
            webhook_transport=LocalReceiver(receiver),
        )
    ) as client:
        yield client, email, password
    relay_engine.dispose()
    provider_engine.dispose()
    receiver_engine.dispose()


def test_admin_runs_lost_response_scenario_and_reads_durable_proof(
    scenario_client: tuple[TestClient, str, str],
) -> None:
    client, email, password = scenario_client
    login = client.post("/api/session/login", json={"email": email, "password": password})
    assert login.status_code == 200
    csrf = login.json()["csrfToken"]
    missing_csrf = client.post(
        "/api/demo/scenarios", json={"scenarioType": "LOST_CAPTURE_RESPONSE"}
    )
    assert missing_csrf.status_code == 403

    created = client.post(
        "/api/demo/scenarios",
        headers={"X-CSRF-Token": csrf},
        json={"scenarioType": "LOST_CAPTURE_RESPONSE"},
    )
    assert created.status_code == 201, created.text
    proof = created.json()
    assert proof["status"] == "SUCCEEDED"
    assert proof["payment_intent_id"].startswith("pay_")
    assert [step["status"] for step in proof["steps"]] == ["COMPLETE"] * 7
    assert (
        proof["assertions"]
        | {
            "providerEffects": 1,
            "captures": 1,
            "journals": 1,
            "events": 1,
            "recipients": 1,
            "deliveries": 1,
            "attachedKeys": 2,
            "stableReplay": True,
            "ledgerBalanced": True,
        }
        == proof["assertions"]
    )

    fetched = client.get(f"/api/demo/scenarios/{proof['scenario_run_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == proof
    absent = client.get(f"/api/demo/scenarios/{new_public_id('scn')}")
    assert absent.status_code == 404

    evidence = client.get(f"/api/v1/payment_intents/{proof['payment_intent_id']}/evidence")
    assert evidence.status_code == 200
    evidence_body = evidence.json()
    capture_event = next(
        event for event in evidence_body["events"] if event["type"] == "payment.captured.v1"
    )
    capture_delivery = next(
        delivery
        for delivery in evidence_body["deliveries"]
        if delivery["eventId"] == capture_event["id"]
    )
    assert capture_delivery["status"] == "DELIVERED"
    assert capture_delivery["attemptCount"] == 1
    delivery_id = capture_delivery["id"]
    delivery = client.get(f"/api/v1/webhook_deliveries/{delivery_id}")
    assert delivery.status_code == 200
    assert delivery.json()["event"]["sha256"] == proof["assertions"]["eventSha256"]
    denied_replay = client.post(f"/api/v1/webhook_deliveries/{delivery_id}/replay")
    assert denied_replay.status_code == 403
    replay = client.post(
        f"/api/v1/webhook_deliveries/{delivery_id}/replay",
        headers={"X-CSRF-Token": csrf},
    )
    assert replay.status_code == 202
    assert replay.json()["status"] == "PENDING"
