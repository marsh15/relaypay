import hashlib
import hmac
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.database import build_engine, build_session_factory
from relaypay.mock_provider.models import ProviderAccount, ProviderEffect
from sqlalchemy import func, select

from apps.provider.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL="postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
        PROVIDER_DATABASE_URL="postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider",
        RECEIVER_DATABASE_URL="postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay",
        SESSION_SECRET="mock-provider-session-secret-at-least-32-bytes",
        CSRF_SECRET="mock-provider-csrf-secret-at-least-32-bytes",
        API_KEY_PEPPER="mock-provider-api-key-pepper-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="mock-provider-idempotency",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-provider-tests",
        PROVIDER_ACCOUNT_ID=f"acct_test_{uuid.uuid4().hex}",
        PROVIDER_SIGNING_SECRET="provider-signing-secret-for-tests",
        PROVIDER_CONTROL_SECRET="provider-control-secret-for-tests",
        RECEIVER_WEBHOOK_SECRET="receiver-webhook-test",
    )


@pytest.fixture
def provider_account(settings: Settings) -> str:
    engine = build_engine(
        settings.PROVIDER_DATABASE_URL.get_secret_value(), application_name="provider-test-seed"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        session.add(
            ProviderAccount(
                public_id=settings.PROVIDER_ACCOUNT_ID,
                name="Provider integration account",
                signing_secret_digest=hashlib.sha256(
                    settings.PROVIDER_SIGNING_SECRET.get_secret_value().encode("utf-8")
                ).digest(),
            )
        )
    engine.dispose()
    return settings.PROVIDER_ACCOUNT_ID


@pytest.fixture
def client(settings: Settings, provider_account: str) -> Iterator[TestClient]:
    del provider_account
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _command(account_id: str, stable_key: str) -> dict[str, object]:
    return {
        "accountId": account_id,
        "stableKey": stable_key,
        "operationKind": "CAPTURE",
        "reference": f"cap_{uuid.uuid4().hex}",
        "amount": 100_000,
        "currency": "INR",
    }


def test_stable_provider_key_creates_one_effect_and_rejects_contradiction(
    client: TestClient, settings: Settings, provider_account: str
) -> None:
    stable_key = f"capture:pay_{uuid.uuid4().hex}"
    command = _command(provider_account, stable_key)
    first = client.post("/v1/effects", json=command)
    replay = client.post("/v1/effects", json=command)
    assert first.status_code == replay.status_code == 200
    assert first.content == replay.content
    expected_signature = hmac.new(
        settings.PROVIDER_SIGNING_SECRET.get_secret_value().encode("utf-8"),
        first.content,
        hashlib.sha256,
    ).hexdigest()
    assert first.headers["X-Provider-Signature"] == expected_signature

    contradictory = dict(command)
    contradictory["amount"] = 100_001
    conflict = client.post("/v1/effects", json=contradictory)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "PROVIDER_KEY_CONFLICT"

    engine = build_engine(
        settings.PROVIDER_DATABASE_URL.get_secret_value(), application_name="provider-count-test"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(ProviderEffect)
                .where(ProviderEffect.stable_key == stable_key)
            )
            == 1
        )
    engine.dispose()


def test_lost_response_still_persists_one_lookupable_effect(
    client: TestClient, settings: Settings, provider_account: str
) -> None:
    stable_key = f"capture:pay_{uuid.uuid4().hex}"
    configured = client.post(
        "/control/faults",
        headers={"X-Provider-Control": settings.PROVIDER_CONTROL_SECRET.get_secret_value()},
        json={
            "accountId": provider_account,
            "stableKey": stable_key,
            "faultType": "LOSE_RESPONSE",
        },
    )
    assert configured.status_code == 204

    mutation = client.post("/v1/effects", json=_command(provider_account, stable_key))
    assert mutation.status_code == 599
    assert mutation.content == b""

    lookup = client.get(f"/v1/effects/{stable_key}", params={"account_id": provider_account})
    assert lookup.status_code == 200
    assert lookup.json()["outcome"] == "SUCCEEDED"
    assert "X-Provider-Signature" in lookup.headers


def test_provider_validation_does_not_echo_unknown_input(
    client: TestClient, provider_account: str
) -> None:
    marker = "provider-sensitive-marker"
    command = _command(provider_account, f"capture:pay_{uuid.uuid4().hex}")
    command["unknown"] = marker
    response = client.post("/v1/effects", json=command)
    assert response.status_code == 422
    assert marker not in response.text
