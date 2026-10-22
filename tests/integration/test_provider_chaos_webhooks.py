"""M17: the provider simulator's seeded chaos profile and its signed webhook push."""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.database import build_engine, build_session_factory
from relaypay.mock_provider.models import ProviderAccount, ProviderEffect, ProviderWebhookOutbox
from relaypay.mock_provider.webhooks import (
    MAX_PUSH_ATTEMPTS,
    WebhookTransport,
    push_webhook_batch,
)
from relaypay.psp.models import PspEvent
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app as create_api
from apps.provider.main import create_app as create_provider
from tests.support.db import relaypay_factory, require_postgres
from tests.support.psp import WEBHOOK_SECRET

pytestmark = [pytest.mark.integration]

INBOX_URL = "http://relaypay.test/api/inbound/v1/psp/simulator"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL="postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
        PROVIDER_DATABASE_URL="postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider",
        RECEIVER_DATABASE_URL="postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay",
        SESSION_SECRET="provider-chaos-session-secret-at-least-32-bytes",
        CSRF_SECRET="provider-chaos-csrf-secret-at-least-32-bytes",
        API_KEY_PEPPER="provider-chaos-api-key-pepper-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="provider-chaos-idem",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-provider-tests",
        PROVIDER_ACCOUNT_ID=f"acct_chaos_{uuid.uuid4().hex}",
        PROVIDER_SIGNING_SECRET="provider-signing-secret-for-tests",
        PROVIDER_CONTROL_SECRET="provider-control-secret-for-tests",
        PROVIDER_WEBHOOK_SECRET=WEBHOOK_SECRET,
        PROVIDER_WEBHOOK_URL=INBOX_URL,
        RECEIVER_WEBHOOK_SECRET="receiver-webhook-test",
    )


@pytest.fixture
def provider_factory(settings: Settings) -> Iterator[sessionmaker[Session]]:
    require_postgres()
    engine = build_engine(
        settings.PROVIDER_DATABASE_URL.get_secret_value(), application_name="provider-chaos-test"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        session.add(
            ProviderAccount(
                public_id=settings.PROVIDER_ACCOUNT_ID,
                name="Provider chaos account",
                signing_secret_digest=hashlib.sha256(b"provider-signing-secret-for-tests").digest(),
            )
        )
    yield factory
    engine.dispose()


@pytest.fixture
def client(settings: Settings, provider_factory: sessionmaker[Session]) -> TestClient:
    del provider_factory
    return TestClient(create_provider(settings))  # no lifespan: the tests drive the push


def _control(settings: Settings) -> dict[str, str]:
    return {"X-Provider-Control": settings.PROVIDER_CONTROL_SECRET.get_secret_value()}


def _chaos(client: TestClient, settings: Settings, **profile: object) -> None:
    response = client.post(
        "/control/chaos",
        headers=_control(settings),
        json={"accountId": settings.PROVIDER_ACCOUNT_ID, "seed": 42} | profile,
    )
    assert response.status_code == 204, response.text


def _mutate(client: TestClient, settings: Settings, stable_key: str) -> int:
    return client.post(
        "/v1/effects",
        json={
            "accountId": settings.PROVIDER_ACCOUNT_ID,
            "stableKey": stable_key,
            "operationKind": "AUTHORIZE",
            "reference": f"pay_{uuid.uuid4().hex}",
            "amount": 100_000,
            "currency": "INR",
        },
    ).status_code


def _key() -> str:
    return f"authorize:pay_{uuid.uuid4().hex}"


def _outbox(factory: sessionmaker[Session], stable_key: str) -> ProviderWebhookOutbox:
    with factory() as session, session.begin():
        event = session.scalar(
            select(ProviderWebhookOutbox)
            .join(ProviderEffect, ProviderEffect.id == ProviderWebhookOutbox.provider_effect_id)
            .where(ProviderEffect.stable_key == stable_key)
        )
        assert event is not None
        session.expunge(event)
        return event


def _effects(factory: sessionmaker[Session], stable_key: str) -> list[str]:
    with factory() as session, session.begin():
        return list(
            session.scalars(
                select(ProviderEffect.outcome).where(ProviderEffect.stable_key == stable_key)
            )
        )


class Recorder:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.sent: list[tuple[dict[str, str], bytes]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> int:
        assert url == INBOX_URL
        self.sent.append((headers, body))
        return self.status


def _push(
    factory: sessionmaker[Session], transport: WebhookTransport, *, now: datetime | None = None
) -> int:
    return push_webhook_batch(
        factory, url=INBOX_URL, secret=WEBHOOK_SECRET, transport=transport, now=now
    )


def test_chaos_control_requires_the_control_secret(client: TestClient, settings: Settings) -> None:
    response = client.post("/control/chaos", json={"accountId": settings.PROVIDER_ACCOUNT_ID})
    assert response.status_code == 401


def test_error_before_commit_creates_no_effect_and_disabling_restores_success(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    key = _key()
    _chaos(client, settings, errorBeforeBps=10_000)
    assert _mutate(client, settings, key) == 503
    assert _effects(provider_factory, key) == []

    _chaos(client, settings, enabled=False)
    assert _mutate(client, settings, key) == 200
    assert _effects(provider_factory, key) == ["SUCCEEDED"]


def test_error_after_commit_keeps_the_effect(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    key = _key()
    _chaos(client, settings, errorAfterBps=10_000, declineBps=10_000)
    assert _mutate(client, settings, key) == 503
    assert _effects(provider_factory, key) == ["DECLINED"]


def test_a_one_shot_directive_wins_over_the_profile(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    key = _key()
    _chaos(client, settings, declineBps=10_000)
    client.post(
        "/control/faults",
        headers=_control(settings),
        json={"accountId": settings.PROVIDER_ACCOUNT_ID, "stableKey": key, "faultType": "PENDING"},
    )
    assert _mutate(client, settings, key) == 200
    assert _effects(provider_factory, key) == ["PENDING"]


def test_each_effect_pushes_one_signed_event_relaypay_accepts(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    key = _key()
    assert _mutate(client, settings, key) == 200
    event = _outbox(provider_factory, key)
    assert (event.status, event.copies_remaining) == ("PENDING", 1)

    with TestClient(create_api()) as api:

        def to_relaypay(url: str, headers: dict[str, str], body: bytes) -> int:
            return api.post(
                url.removeprefix("http://relaypay.test"), headers=headers, content=body
            ).status_code

        assert _push(provider_factory, to_relaypay) >= 1

    assert _outbox(provider_factory, key).status == "DELIVERED"
    with relaypay_factory("provider-chaos-inbox") as factory, factory() as session, session.begin():
        stored = session.scalar(
            select(PspEvent).where(PspEvent.provider_event_id == event.event_id)
        )
        assert stored is not None
        assert (stored.provider, stored.stable_key, stored.status) == (
            "SIMULATOR",
            key,
            "UNMATCHED",
        )


def test_a_duplicated_event_is_delivered_twice_with_the_same_bytes(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    key = _key()
    _chaos(client, settings, webhookDuplicateBps=10_000)
    assert _mutate(client, settings, key) == 200
    event = _outbox(provider_factory, key)
    recorder = Recorder()

    _push(provider_factory, recorder)
    _push(provider_factory, recorder)
    _push(provider_factory, recorder)

    mine = [
        body for headers, body in recorder.sent if headers["X-Provider-Event-Id"] == event.event_id
    ]
    assert mine == [event.payload_bytes, event.payload_bytes]
    assert _outbox(provider_factory, key).status == "DELIVERED"


def test_a_dropped_event_is_kept_but_never_sent(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    key = _key()
    _chaos(client, settings, webhookDropBps=10_000)
    assert _mutate(client, settings, key) == 200
    event = _outbox(provider_factory, key)
    recorder = Recorder()

    _push(provider_factory, recorder)

    assert event.status == "DROPPED"
    assert all(headers["X-Provider-Event-Id"] != event.event_id for headers, _ in recorder.sent)


def test_failed_pushes_back_off_and_then_dead_letter(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    key = _key()
    assert _mutate(client, settings, key) == 200
    failing = Recorder(status=503)
    moment = datetime.now(UTC)

    for _ in range(MAX_PUSH_ATTEMPTS):
        _push(provider_factory, failing, now=moment)
        event = _outbox(provider_factory, key)
        if event.status == "DEAD":
            break
        assert event.next_attempt_at > moment
        moment = event.next_attempt_at + timedelta(milliseconds=1)

    event = _outbox(provider_factory, key)
    assert (event.status, event.attempt_count, event.last_http_status) == (
        "DEAD",
        MAX_PUSH_ATTEMPTS,
        503,
    )


def test_the_same_seed_replays_the_same_outcomes(
    client: TestClient, settings: Settings, provider_factory: sessionmaker[Session]
) -> None:
    profile = {"seed": 99, "declineBps": 5_000, "pendingBps": 2_000}
    keys = [_key() for _ in range(12)]
    _chaos(client, settings, **profile)
    for key in keys:
        _mutate(client, settings, key)
    first = [_effects(provider_factory, key) for key in keys]

    other = settings.model_copy(update={"PROVIDER_ACCOUNT_ID": f"acct_chaos_{uuid.uuid4().hex}"})
    with provider_factory() as session, session.begin():
        session.add(
            ProviderAccount(
                public_id=other.PROVIDER_ACCOUNT_ID,
                name="Replay account",
                signing_secret_digest=hashlib.sha256(b"x").digest(),
            )
        )
    _chaos(client, other, **profile)
    for key in keys:
        _mutate(client, other, key)
    with provider_factory() as session, session.begin():
        replay = [
            list(
                session.scalars(
                    select(ProviderEffect.outcome)
                    .join(ProviderAccount)
                    .where(
                        ProviderAccount.public_id == other.PROVIDER_ACCOUNT_ID,
                        ProviderEffect.stable_key == key,
                    )
                )
            )
            for key in keys
        ]
    assert replay == first
    assert len({outcome for [outcome] in first}) > 1  # the profile actually varied outcomes
    with provider_factory() as session, session.begin():
        assert session.scalar(
            select(func.count())
            .select_from(ProviderEffect)
            .where(ProviderEffect.stable_key.in_(keys))
        ) == 2 * len(keys)
