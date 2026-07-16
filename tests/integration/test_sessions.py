from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import Organisation, SessionRecord, User
from relaypay.identity.security import hash_password
from relaypay.ids import new_public_id
from sqlalchemy import delete, select

from apps.api.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL="postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
        PROVIDER_DATABASE_URL="postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider",
        RECEIVER_DATABASE_URL="postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay",
        SESSION_SECRET="session-secret-for-tests-at-least-32-bytes",
        CSRF_SECRET="csrf-secret-for-tests-at-least-32-bytes",
        API_KEY_PEPPER="api-key-pepper-for-tests-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="idempotency-pepper-tests",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-session-tests",
        PROVIDER_SIGNING_SECRET="provider-signing-test",
        PROVIDER_CONTROL_SECRET="provider-control-test",
        RECEIVER_WEBHOOK_SECRET="receiver-webhook-test",
    )


@pytest.fixture
def seeded_admin(settings: Settings) -> tuple[str, str]:
    email = f"admin-{new_public_id('usr')}@example.test"
    password = "Correct-Horse-Battery-Staple!"
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="session-test-seed"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="Session tests", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        session.add(
            User(
                organisation_id=organisation.id,
                email_normalized=email.casefold(),
                display_name="Session administrator",
                password_hash=hash_password(password),
                role="ADMIN",
                status="ACTIVE",
            )
        )
    engine.dispose()
    return email, password


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_login_me_csrf_logout_flow(
    client: TestClient, settings: Settings, seeded_admin: tuple[str, str]
) -> None:
    email, password = seeded_admin
    login = client.post("/api/session/login", json={"email": email, "password": password})
    assert login.status_code == 200
    assert login.json()["organisationId"].startswith("org_")
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "samesite=lax" in login.headers["set-cookie"].lower()
    assert login.headers["x-content-type-options"] == "nosniff"

    me = client.get("/api/session/me")
    assert me.status_code == 200, me.text
    csrf_token = me.json()["csrfToken"]

    missing_csrf = client.post("/api/session/logout")
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "CSRF_INVALID"

    logout = client.post("/api/session/logout", headers={"X-CSRF-Token": csrf_token})
    assert logout.status_code == 200
    assert client.get("/api/session/me").status_code == 401


def test_invalid_login_is_generic(client: TestClient, seeded_admin: tuple[str, str]) -> None:
    email, _ = seeded_admin
    response = client.post(
        "/api/session/login", json={"email": email, "password": "Wrong-Password"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "INVALID_CREDENTIALS",
        "message": "Email or password is incorrect",
        "details": None,
    }


def test_validation_response_never_echoes_password(client: TestClient) -> None:
    marker = "Top-Secret-Password-Marker"
    response = client.post(
        "/api/session/login",
        json={"email": "not-an-email", "password": marker, "unexpected": True},
    )
    assert response.status_code == 422
    assert marker not in response.text


def test_login_rate_limit_has_retry_after(client: TestClient) -> None:
    for _ in range(5):
        client.post(
            "/api/session/login",
            json={"email": "nobody@example.test", "password": "Wrong-Password"},
        )
    response = client.post(
        "/api/session/login",
        json={"email": "nobody@example.test", "password": "Wrong-Password"},
    )
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_session_token_is_stored_only_as_digest(
    client: TestClient, settings: Settings, seeded_admin: tuple[str, str]
) -> None:
    email, password = seeded_admin
    response = client.post("/api/session/login", json={"email": email, "password": password})
    raw_cookie = client.cookies.get(settings.SESSION_COOKIE_NAME)
    assert raw_cookie is not None

    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="session-storage-test"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        digests = session.scalars(select(SessionRecord.token_digest)).all()
        assert all(digest != raw_cookie.encode("utf-8") for digest in digests)
        session.execute(delete(SessionRecord).where(SessionRecord.revoked_at.is_not(None)))
    engine.dispose()
    assert response.status_code == 200


def test_retry_lookup_contract_rejects_manual_outcome_assertion(
    client: TestClient, seeded_admin: tuple[str, str]
) -> None:
    email, password = seeded_admin
    login = client.post("/api/session/login", json={"email": email, "password": password})
    response = client.post(
        "/api/v1/operations/op_00000000000000000000000000000000/retry_lookup",
        headers={"X-CSRF-Token": login.json()["csrfToken"]},
        json={"outcome": "SUCCEEDED"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
