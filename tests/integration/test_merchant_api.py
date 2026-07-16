import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import APIKey, Organisation
from relaypay.identity.security import issue_api_key
from relaypay.ids import new_public_id

from apps.api.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL="postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
        PROVIDER_DATABASE_URL="postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider",
        RECEIVER_DATABASE_URL="postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay",
        SESSION_SECRET="merchant-api-session-secret-at-least-32-bytes",
        CSRF_SECRET="merchant-api-csrf-secret-at-least-32-bytes",
        API_KEY_PEPPER="merchant-api-key-pepper-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="merchant-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-merchant-tests",
        PROVIDER_SIGNING_SECRET="provider-signing-test",
        PROVIDER_CONTROL_SECRET="provider-control-test",
        RECEIVER_WEBHOOK_SECRET="receiver-webhook-test",
    )


@pytest.fixture
def merchant_keys(settings: Settings) -> tuple[str, str]:
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="merchant-api-seed"
    )
    factory = build_session_factory(engine)
    keys: list[str] = []
    with factory() as session, session.begin():
        for label in ("First", "Second"):
            organisation = Organisation(
                public_id=new_public_id("org"), name=f"{label} merchant", status="ACTIVE"
            )
            session.add(organisation)
            session.flush()
            issued, digest = issue_api_key(pepper=settings.API_KEY_PEPPER.get_secret_value())
            session.add(
                APIKey(
                    organisation_id=organisation.id,
                    name=f"{label} merchant key",
                    public_prefix=issued.public_prefix,
                    secret_digest=digest,
                    scopes=["customers:write", "payments:read", "payments:write"],
                    status="ACTIVE",
                )
            )
            keys.append(issued.plaintext)
    engine.dispose()
    return keys[0], keys[1]


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _authorization(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_merchant_payment_contract_and_cross_tenant_404(
    client: TestClient, merchant_keys: tuple[str, str]
) -> None:
    first_key, second_key = merchant_keys
    customer = client.post(
        "/api/v1/customers",
        headers=_authorization(first_key),
        json={
            "merchant_customer_reference": f"customer-{uuid.uuid4().hex}",
            "display_name": "Synthetic API customer",
        },
    )
    assert customer.status_code == 201

    payment_payload = {
        "customer_id": customer.json()["id"],
        "merchant_reference": f"order-{uuid.uuid4().hex}",
        "amount": 125_000,
        "currency": "INR",
    }
    missing_key = client.post(
        "/api/v1/payment_intents",
        headers=_authorization(first_key),
        json=payment_payload,
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    idempotency_key = f"payment-{uuid.uuid4().hex}"
    headers = {**_authorization(first_key), "Idempotency-Key": idempotency_key}
    created = client.post("/api/v1/payment_intents", headers=headers, json=payment_payload)
    replayed = client.post("/api/v1/payment_intents", headers=headers, json=payment_payload)
    assert created.status_code == replayed.status_code == 201
    assert created.content == replayed.content
    assert replayed.headers["Idempotency-Replayed"] == "true"

    payment_id = created.json()["id"]
    cross_tenant = client.get(
        f"/api/v1/payment_intents/{payment_id}", headers=_authorization(second_key)
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    authorize_a = client.post(
        f"/api/v1/payment_intents/{payment_id}/authorize",
        headers={**_authorization(first_key), "Idempotency-Key": f"auth-a-{uuid.uuid4().hex}"},
        json={},
    )
    authorize_b = client.post(
        f"/api/v1/payment_intents/{payment_id}/authorize",
        headers={**_authorization(first_key), "Idempotency-Key": f"auth-b-{uuid.uuid4().hex}"},
        json={},
    )
    assert authorize_a.status_code == authorize_b.status_code == 202
    assert authorize_a.content == authorize_b.content


def test_financial_routes_reject_unknown_fields_without_echoing_values(
    client: TestClient, merchant_keys: tuple[str, str]
) -> None:
    first_key, _ = merchant_keys
    marker = "sensitive-marker-never-echo"
    response = client.post(
        "/api/v1/payment_intents",
        headers={**_authorization(first_key), "Idempotency-Key": "unknown-field-test"},
        json={
            "customer_id": "cus_" + "a" * 32,
            "merchant_reference": "order",
            "amount": 100,
            "currency": "INR",
            "unknown": marker,
        },
    )
    assert response.status_code == 422
    assert marker not in response.text
