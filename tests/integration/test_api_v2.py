"""/api/v2 over HTTP: hold, partial capture, void, and the v2 payment view."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app
from tests.support.db import (
    provider_database_url,
    relaypay_database_url,
    relaypay_factory,
    require_postgres,
)
from tests.support.fake_provider import ACCOUNT_ID, SIGNING_SECRET, FakeProvider
from tests.support.world import World, _api_key_pepper, build_world, create_payment

pytestmark = [pytest.mark.integration]


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL=relaypay_database_url(),
        PROVIDER_DATABASE_URL=provider_database_url(),
        RECEIVER_DATABASE_URL=relaypay_database_url(),
        SESSION_SECRET="api-v2-session-secret-at-least-32-bytes-long",
        CSRF_SECRET="api-v2-csrf-secret-at-least-32-bytes-long",
        API_KEY_PEPPER=_api_key_pepper(),
        IDEMPOTENCY_KEY_PEPPER="api-v2-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-api-v2-tests",
        PROVIDER_ACCOUNT_ID=ACCOUNT_ID,
        PROVIDER_SIGNING_SECRET=SIGNING_SECRET,
        PROVIDER_CONTROL_SECRET="api-v2-provider-control",
        RECEIVER_WEBHOOK_SECRET="api-v2-receiver-webhook",
    )


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("api-v2-tests", pool_size=4) as session_factory:
        yield session_factory


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings(), provider_transport=FakeProvider())) as test_client:
        yield test_client


def _headers(world: World, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {world.api_key}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _post(
    client: TestClient, world: World, path: str, body: dict[str, object]
) -> dict[str, object]:
    response = client.post(path, json=body, headers=_headers(world, f"k-{uuid.uuid4().hex}"))
    assert response.status_code in {200, 202}, response.text
    return dict(response.json())


def test_hold_partial_capture_and_v2_view(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    world = build_world(factory, label="api-v2-capture")
    payment = create_payment(factory, world, amount=100_000)
    base = f"/api/v2/payment_intents/{payment}"

    authorized = _post(client, world, f"{base}/authorize", {"hold_seconds": 3600})
    assert authorized["status"] == "SUCCEEDED"
    captured = _post(client, world, f"{base}/capture", {"amount": 60_000})
    assert (captured["status"], captured["amount"]) == ("SUCCEEDED", 60_000)

    view = client.get(base, headers=_headers(world)).json()
    assert (view["status"], view["statusVersion"]) == ("captured", 2)
    assert view["authorization"]["holdSeconds"] == 3600
    assert view["authorization"]["expiresAt"] is not None
    assert view["capture"] == {
        "id": view["capture"]["id"],
        "status": "SUCCEEDED",
        "amount": 60_000,
        "feeAmount": 0,
        "gstAmount": 0,
        "netAmount": 60_000,
    }
    assert (view["capturedAmount"], view["refundableAmount"], view["void"]) == (
        60_000,
        60_000,
        None,
    )


def test_void_over_http_cancels_and_blocks_capture(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    world = build_world(factory, label="api-v2-void")
    payment = create_payment(factory, world)
    base = f"/api/v2/payment_intents/{payment}"
    _post(client, world, f"{base}/authorize", {})

    voided = _post(client, world, f"{base}/void", {})

    assert voided["status"] == "SUCCEEDED"
    view = client.get(base, headers=_headers(world)).json()
    assert view["status"] == "cancelled"
    assert view["void"]["status"] == "SUCCEEDED"
    refused = client.post(f"{base}/capture", json={}, headers=_headers(world, "late-capture"))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "VOID_IN_PROGRESS"


def test_v2_validates_bodies_and_keeps_v1_frozen(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    world = build_world(factory, label="api-v2-validation")
    payment = create_payment(factory, world)
    v2 = f"/api/v2/payment_intents/{payment}"

    too_short = client.post(
        f"{v2}/authorize", json={"hold_seconds": 59}, headers=_headers(world, "short-hold")
    )
    assert too_short.status_code == 422
    unknown = client.post(f"{v2}/capture", json={"surprise": 1}, headers=_headers(world, "x"))
    assert unknown.status_code == 422

    # v1 read keeps its frozen shape: no v2 fields leak into it.
    v1 = client.get(f"/api/v1/payment_intents/{payment}", headers=_headers(world)).json()
    assert set(v1) == {
        "id",
        "merchantReference",
        "amount",
        "currency",
        "authorizationStatus",
        "captureStatus",
        "capturedAmount",
        "reservedOrRefundedAmount",
        "refundableAmount",
    }


def test_a_key_used_on_v1_cannot_replay_a_v2_command(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    world = build_world(factory, label="api-v2-fingerprint")
    payment = create_payment(factory, world)
    key = "shared-across-versions"
    first = client.post(
        f"/api/v1/payment_intents/{payment}/authorize", json={}, headers=_headers(world, key)
    )
    assert first.status_code in {200, 202}

    reused = client.post(
        f"/api/v2/payment_intents/{payment}/authorize", json={}, headers=_headers(world, key)
    )

    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
