"""M19 T19.1: per-API-key GCRA rate limiting on an UNLOGGED Postgres table."""

import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import APIKey, APIKeyVersion, Environment, Organisation
from relaypay.identity.rate_limit import GcraLimiter, prune_rate_limit_state
from relaypay.identity.security import issue_api_key
from relaypay.ids import new_public_id
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine

from apps.api.main import create_app
from tests.support.db import require_postgres

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"


@pytest.fixture
def engine() -> Iterator[Engine]:
    require_postgres()
    built = create_engine(DATABASE_URL, pool_size=40, isolation_level="AUTOCOMMIT")
    yield built
    built.dispose()


def _key() -> str:
    return f"test:{uuid.uuid4().hex}"


def test_a_rested_key_may_burst_then_is_held_to_its_rate(engine: Engine) -> None:
    limiter = GcraLimiter(engine)
    key = _key()

    admitted = [limiter.admit(key, rps=10, burst=5).allowed for _ in range(5)]
    refused = limiter.admit(key, rps=10, burst=5)

    assert admitted == [True] * 5
    assert not refused.allowed and refused.retry_after_seconds >= 1
    time.sleep(0.35)  # three emission intervals at 10 rps
    assert limiter.admit(key, rps=10, burst=5).allowed


def test_a_refused_request_does_not_spend_budget(engine: Engine) -> None:
    limiter = GcraLimiter(engine)
    key = _key()
    assert limiter.admit(key, rps=2, burst=1).allowed
    for _ in range(20):
        assert not limiter.admit(key, rps=2, burst=1).allowed
    time.sleep(0.55)  # one interval at 2 rps; 20 refusals must not have pushed it out
    assert limiter.admit(key, rps=2, burst=1).allowed


def test_keys_are_independent(engine: Engine) -> None:
    limiter = GcraLimiter(engine)
    noisy, quiet = _key(), _key()
    assert limiter.admit(noisy, rps=1, burst=1).allowed
    assert not limiter.admit(noisy, rps=1, burst=1).allowed
    assert limiter.admit(quiet, rps=1, burst=1).allowed


def test_concurrent_callers_never_exceed_the_burst(engine: Engine) -> None:
    limiter = GcraLimiter(engine)
    key = _key()
    burst, callers = 20, 200

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(
            pool.map(lambda _: limiter.admit(key, rps=1, burst=burst).allowed, range(callers))
        )
    elapsed = time.monotonic() - started

    # At 1 rps the budget refills one request per second, so the burst plus what the run's own
    # duration earned is the ceiling. It is exact when the run takes under a second.
    assert burst <= sum(results) <= burst + int(elapsed) + 1


def test_the_limiter_fails_open_when_postgres_is_unreachable() -> None:
    dead = create_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    decision = GcraLimiter(dead).admit(_key(), rps=1, burst=1)
    dead.dispose()

    assert decision.allowed and decision.failed_open


def test_prune_removes_only_long_lapsed_state(engine: Engine) -> None:
    stale, fresh = _key(), _key()
    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO api_rate_limit_state (key, tat) VALUES "
                "(:stale, now() - interval '2 hours'), (:fresh, now() + interval '1 minute')"
            ),
            {"stale": stale, "fresh": fresh},
        )
    factory = build_session_factory(engine)

    assert prune_rate_limit_state(factory, older_than_seconds=3600) >= 1

    with engine.connect() as connection:
        remaining = set(
            connection.execute(
                text("SELECT key FROM api_rate_limit_state WHERE key IN (:a, :b)"),
                {"a": stale, "b": fresh},
            ).scalars()
        )
    assert remaining == {fresh}


def _settings(**overrides: object) -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL=DATABASE_URL,
        PROVIDER_DATABASE_URL="postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider",
        RECEIVER_DATABASE_URL="postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay",
        SESSION_SECRET="rate-limit-session-secret-at-least-32-bytes",
        CSRF_SECRET="rate-limit-csrf-secret-at-least-32-bytes",
        API_KEY_PEPPER="rate-limit-key-pepper-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="rate-limit-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-rate-limit-tests",
        PROVIDER_SIGNING_SECRET="provider-signing-test",
        PROVIDER_CONTROL_SECRET="provider-control-test",
        RECEIVER_WEBHOOK_SECRET="receiver-webhook-test",
        **overrides,  # type: ignore[arg-type]
    )


def _merchant_key(
    settings: Settings, *, rate_limit_rps: int | None = None, rate_limit_burst: int | None = None
) -> str:
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="rate-limit-seed"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="Rate limit merchant", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        environment = session.scalar(
            select(Environment).where(
                Environment.organisation_id == organisation.id,
                Environment.environment_type == "TEST",
            )
        )
        assert environment is not None
        issued, digest = issue_api_key(pepper=settings.API_KEY_PEPPER.get_secret_value())
        record = APIKey(
            public_id=new_public_id("key"),
            organisation_id=organisation.id,
            environment_id=environment.id,
            name="Rate limited key",
            scopes=["payments:read"],
            status="ACTIVE",
            rate_limit_rps=rate_limit_rps,
            rate_limit_burst=rate_limit_burst,
        )
        session.add(record)
        session.flush()
        session.add(
            APIKeyVersion(
                organisation_id=organisation.id,
                environment_id=environment.id,
                api_key_id=record.id,
                version=1,
                public_prefix=issued.public_prefix,
                secret_digest=digest,
                status="ACTIVE",
            )
        )
    engine.dispose()
    return issued.plaintext


def _list(client: TestClient, key: str) -> int:
    return client.get(
        "/api/v1/payment_intents", headers={"Authorization": f"Bearer {key}"}
    ).status_code


def test_http_requests_past_the_burst_get_429_with_retry_after() -> None:
    settings = _settings(API_RATE_LIMIT_RPS=1, API_RATE_LIMIT_BURST=3)
    noisy, quiet = _merchant_key(settings), _merchant_key(settings)
    with TestClient(create_app(settings)) as client:
        statuses = [_list(client, noisy) for _ in range(5)]
        limited = client.get(
            "/api/v1/payment_intents", headers={"Authorization": f"Bearer {noisy}"}
        )
        quiet_status = _list(client, quiet)

    assert statuses[:3] == [200, 200, 200]
    assert statuses[3:] == [429, 429]
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) >= 1
    assert quiet_status == 200


def test_a_key_override_beats_the_deployment_default() -> None:
    settings = _settings(API_RATE_LIMIT_RPS=100, API_RATE_LIMIT_BURST=100)
    strict = _merchant_key(settings, rate_limit_rps=1, rate_limit_burst=1)
    with TestClient(create_app(settings)) as client:
        assert [_list(client, strict) for _ in range(2)] == [200, 429]


def test_a_bad_credential_is_refused_before_it_can_spend_anyone_s_budget() -> None:
    settings = _settings(API_RATE_LIMIT_RPS=1, API_RATE_LIMIT_BURST=1)
    with TestClient(create_app(settings)) as client:
        statuses = [_list(client, f"rpk_test_nope.{uuid.uuid4().hex}") for _ in range(3)]
    assert statuses == [401, 401, 401]


def test_the_limit_can_be_switched_off() -> None:
    settings = _settings(API_RATE_LIMIT_ENABLED=False, API_RATE_LIMIT_RPS=1, API_RATE_LIMIT_BURST=1)
    key = _merchant_key(settings)
    with TestClient(create_app(settings)) as client:
        assert [_list(client, key) for _ in range(4)] == [200] * 4
