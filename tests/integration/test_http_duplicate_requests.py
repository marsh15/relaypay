"""Duplicate HTTP requests through a real uvicorn server collapse to one operation.

50 concurrent identical ``POST /api/v1/payment_intents`` (same Idempotency-Key and body) must
create exactly one payment intent and return identical statuses and byte-identical bodies;
the same key with a different body is 409 IDEMPOTENCY_KEY_REUSED. 50 concurrent identical
captures must cause exactly one provider mutation and identical bodies.

Headers allowed to differ between responses: ``Idempotency-Replayed`` (absent on the single
winner, ``true`` on replays), ``X-Request-ID``, ``Server-Timing`` and ``Date`` (per request).
"""

import json
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from relaypay.config import Settings
from relaypay.idempotency import canonical_json_bytes
from relaypay.payments.models import PaymentIntent
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app
from tests.support.db import (
    provider_database_url,
    relaypay_database_url,
    relaypay_factory,
    require_postgres,
    run_in_transaction,
)
from tests.support.fake_provider import ACCOUNT_ID, SIGNING_SECRET, FakeProvider
from tests.support.world import World, _api_key_pepper, authorize, build_world, create_payment

pytestmark = [pytest.mark.integration, pytest.mark.proof, pytest.mark.concurrency]

CONCURRENCY = 50
PER_REQUEST_HEADERS = frozenset({"idempotency-replayed", "x-request-id", "server-timing", "date"})


@dataclass(frozen=True, slots=True)
class Reply:
    status: int
    body: bytes
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class Server:
    base_url: str
    provider: FakeProvider


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL=relaypay_database_url(),
        PROVIDER_DATABASE_URL=provider_database_url(),
        RECEIVER_DATABASE_URL=relaypay_database_url(),
        SESSION_SECRET="http-duplicate-session-secret-at-least-32-bytes",
        CSRF_SECRET="http-duplicate-csrf-secret-at-least-32-bytes",
        API_KEY_PEPPER=_api_key_pepper(),  # the pepper build_world issued its key with
        IDEMPOTENCY_KEY_PEPPER="http-duplicate-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-http-duplicate-tests",
        PROVIDER_ACCOUNT_ID=ACCOUNT_ID,
        PROVIDER_SIGNING_SECRET=SIGNING_SECRET,
        PROVIDER_CONTROL_SECRET="http-duplicate-provider-control",
        RECEIVER_WEBHOOK_SECRET="http-duplicate-receiver-webhook",
    )


@pytest.fixture(scope="module")
def server() -> Iterator[Server]:
    require_postgres()
    provider = FakeProvider()
    app = create_app(_settings(), provider_transport=provider)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    uvicorn_server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(
        target=uvicorn_server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 20
    while not uvicorn_server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield Server(f"http://127.0.0.1:{port}", provider)
    finally:
        uvicorn_server.should_exit = True
        thread.join(timeout=20)
        listener.close()


@pytest.fixture(scope="module")
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("http-duplicate-tests") as session_factory:
        yield session_factory


@pytest.fixture
def world(factory: sessionmaker[Session]) -> World:
    return build_world(factory, label="http-duplicate")


def _post(server: Server, world: World, path: str, key: str, body: dict[str, object]) -> Reply:
    with httpx.Client(base_url=server.base_url, timeout=60) as client:
        return _send(client, world, path, key, body)


def _send(
    client: httpx.Client, world: World, path: str, key: str, body: dict[str, object]
) -> Reply:
    response = client.post(
        path,
        content=canonical_json_bytes(body),
        headers={
            "Authorization": f"Bearer {world.api_key}",
            "Idempotency-Key": key,
            "Content-Type": "application/json",
        },
    )
    return Reply(response.status_code, response.content, dict(response.headers))


def _fire_concurrently(
    server: Server, world: World, path: str, key: str, body: dict[str, object]
) -> list[Reply]:
    barrier = threading.Barrier(CONCURRENCY, timeout=60)
    replies: list[Reply | BaseException] = [RuntimeError("no reply")] * CONCURRENCY

    def worker(slot: int) -> None:
        try:
            with httpx.Client(base_url=server.base_url, timeout=60) as client:
                barrier.wait()
                replies[slot] = _send(client, world, path, key, body)
        except BaseException as error:  # every failure must be reported
            replies[slot] = error

    threads = [threading.Thread(target=worker, args=(slot,)) for slot in range(CONCURRENCY)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    failures = [reply for reply in replies if isinstance(reply, BaseException)]
    assert not failures, f"requests failed: {[repr(f) for f in failures[:3]]}"
    return [reply for reply in replies if isinstance(reply, Reply)]


def _stable_headers(reply: Reply) -> dict[str, str]:
    return {k.lower(): v for k, v in reply.headers.items() if k.lower() not in PER_REQUEST_HEADERS}


def _assert_identical(replies: list[Reply]) -> None:
    first = replies[0]
    assert {reply.status for reply in replies} == {first.status}, sorted(
        {reply.status for reply in replies}
    )
    distinct_bodies = {reply.body for reply in replies}
    assert len(distinct_bodies) == 1, f"{len(distinct_bodies)} distinct bodies: {distinct_bodies}"
    assert all(_stable_headers(reply) == _stable_headers(first) for reply in replies)


def _replayed_count(replies: list[Reply]) -> int:
    return sum(
        1 for reply in replies if reply.headers.get("idempotency-replayed", "").lower() == "true"
    )


def test_concurrent_identical_create_makes_one_payment_intent(
    server: Server, world: World, factory: sessionmaker[Session]
) -> None:
    reference = f"order-{uuid.uuid4().hex}"
    key = f"create-{uuid.uuid4().hex}"
    body: dict[str, object] = {
        "customer_id": world.customer_public_id,
        "merchant_reference": reference,
        "amount": 123_456,
        "currency": "INR",
    }

    replies = _fire_concurrently(server, world, "/api/v1/payment_intents", key, body)

    assert replies[0].status == 201, replies[0].body
    _assert_identical(replies)
    # exactly one request won and wrote; every other response is a stored replay
    assert _replayed_count(replies) == CONCURRENCY - 1

    def rows(session: Session) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(PaymentIntent)
                .where(
                    PaymentIntent.organisation_id == world.organisation_id,
                    PaymentIntent.merchant_reference == reference,
                )
            )
            or 0
        )

    assert run_in_transaction(factory, rows) == 1

    # same key, different body: refused, and still exactly one payment intent
    different = _post(server, world, "/api/v1/payment_intents", key, {**body, "amount": 123_457})
    assert different.status == 409
    assert b"IDEMPOTENCY_KEY_REUSED" in different.body
    assert run_in_transaction(factory, rows) == 1


def test_concurrent_identical_capture_sends_one_provider_mutation(
    server: Server, world: World, factory: sessionmaker[Session]
) -> None:
    payment_id = create_payment(factory, world, amount=100_000)
    authorize(factory, world, payment_id, server.provider)
    mutations_before = server.provider.mutation_count()
    key = f"capture-{uuid.uuid4().hex}"

    replies = _fire_concurrently(
        server, world, f"/api/v1/payment_intents/{payment_id}/capture", key, {}
    )

    _assert_identical(replies)
    assert replies[0].status == 200, replies[0].body
    assert json.loads(replies[0].body)["status"] == "SUCCEEDED"
    assert server.provider.mutation_count() - mutations_before == 1
    server.provider.assert_exactly_once()
