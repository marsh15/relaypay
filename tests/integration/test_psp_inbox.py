"""M17: inbound PSP webhooks are verified, matched, and stored exactly once."""

import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from relaypay.errors import RelayPayError
from relaypay.provider_operations.models import ProviderOperation
from relaypay.psp.inbox import accept_psp_event
from relaypay.psp.models import PspEvent
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app
from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.psp import WEBHOOK_SECRET, signature_for, signed_event
from tests.support.world import World, authorize, build_world, create_payment

pytestmark = [pytest.mark.integration]

Mutation = Callable[[dict[str, str], bytes], tuple[dict[str, str], bytes]]


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("psp-inbox-tests", pool_size=10) as session_factory:
        yield session_factory


def _operation(factory: sessionmaker[Session], label: str) -> tuple[World, ProviderOperation]:
    world = build_world(factory, label=label)
    payment = create_payment(factory, world)
    operation_id = authorize(factory, world, payment, FakeProvider())
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None
        session.expunge(operation)
        return world, operation


def _accept(
    factory: sessionmaker[Session], headers: dict[str, str], body: bytes
) -> tuple[PspEvent, bool]:
    return accept_psp_event(
        factory,
        provider="SIMULATOR",
        headers=headers,
        body=body,
        webhook_secret=WEBHOOK_SECRET,
        replay_seconds=300,
    )


def _count(factory: sessionmaker[Session], event_id: str) -> int:
    with factory() as session, session.begin():
        return int(
            session.scalar(
                select(func.count())
                .select_from(PspEvent)
                .where(PspEvent.provider_event_id == event_id)
            )
            or 0
        )


def test_verified_event_is_matched_to_its_operation(factory: sessionmaker[Session]) -> None:
    world, operation = _operation(factory, "psp-matched")
    headers, body = signed_event(
        {"stableKey": operation.stable_provider_key, "outcome": "SUCCEEDED"}
    )

    event, replayed = _accept(factory, headers, body)

    assert not replayed
    assert (event.status, event.provider_operation_id) == ("PENDING", operation.id)
    assert (event.organisation_id, event.environment_id) == (
        world.organisation_id,
        world.environment_id,
    )
    assert event.payload_bytes == body


def test_redelivery_is_a_replay_and_conflicting_bytes_are_refused(
    factory: sessionmaker[Session],
) -> None:
    _, operation = _operation(factory, "psp-replay")
    headers, body = signed_event({"stableKey": operation.stable_provider_key, "outcome": "PENDING"})
    first, _ = _accept(factory, headers, body)

    again, replayed = _accept(factory, headers, body)
    assert replayed and again.id == first.id

    changed_headers, changed_body = signed_event(
        {"stableKey": operation.stable_provider_key, "outcome": "SUCCEEDED"},
        event_id=headers["X-Provider-Event-Id"],
    )
    with pytest.raises(RelayPayError) as conflict:
        _accept(factory, changed_headers, changed_body)
    assert conflict.value.code == "PSP_EVENT_CONFLICT"
    assert _count(factory, headers["X-Provider-Event-Id"]) == 1


def test_unknown_stable_key_is_kept_as_unmatched(factory: sessionmaker[Session]) -> None:
    headers, body = signed_event({"stableKey": "capture:pay_neverexisted", "outcome": "SUCCEEDED"})

    event, _ = _accept(factory, headers, body)

    assert (event.status, event.provider_operation_id, event.organisation_id) == (
        "UNMATCHED",
        None,
        None,
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda h, b: ({**h, "X-Provider-Signature": "v1=" + "0" * 64}, b),
            "INVALID_WEBHOOK_SIGNATURE",
        ),
        (lambda h, b: (h, b + b" "), "INVALID_WEBHOOK_SIGNATURE"),
        (
            lambda h, b: ({k: v for k, v in h.items() if k != "X-Provider-Signature"}, b),
            "PSP_WEBHOOK_HEADERS_MISSING",
        ),
    ],
)
def test_unauthenticated_input_is_refused_before_it_is_parsed(
    factory: sessionmaker[Session], mutate: Mutation, code: str
) -> None:
    headers, body = signed_event({"stableKey": "authorize:pay_x", "outcome": "SUCCEEDED"})
    bad_headers, bad_body = mutate(headers, body)

    with pytest.raises(RelayPayError) as refused:
        _accept(factory, bad_headers, bad_body)

    assert (refused.value.code, refused.value.http_status) == (code, 401)
    assert _count(factory, headers["X-Provider-Event-Id"]) == 0


def test_stale_signed_event_is_refused(factory: sessionmaker[Session]) -> None:
    headers, body = signed_event(
        {"stableKey": "authorize:pay_x", "outcome": "SUCCEEDED"},
        timestamp=int(time.time()) - 3600,
    )
    with pytest.raises(RelayPayError) as refused:
        _accept(factory, headers, body)
    assert refused.value.code == "WEBHOOK_REPLAY_WINDOW_EXCEEDED"


def test_body_id_must_match_the_signed_header(factory: sessionmaker[Session]) -> None:
    headers, body = signed_event({"stableKey": "authorize:pay_x", "outcome": "SUCCEEDED"})
    _, other_body = signed_event({"stableKey": "authorize:pay_x", "outcome": "SUCCEEDED"})
    signature = signature_for(other_body, headers["X-Provider-Timestamp"])
    with pytest.raises(RelayPayError) as refused:
        _accept(factory, {**headers, "X-Provider-Signature": signature}, other_body)
    assert refused.value.code == "PSP_EVENT_ID_MISMATCH"


def test_raw_bytes_are_frozen_but_the_lifecycle_moves(factory: sessionmaker[Session]) -> None:
    _, operation = _operation(factory, "psp-frozen")
    headers, body = signed_event({"stableKey": operation.stable_provider_key, "outcome": "PENDING"})
    event, _ = _accept(factory, headers, body)

    with factory() as session, session.begin():
        session.execute(update(PspEvent).where(PspEvent.id == event.id).values(status="IGNORED"))
    with pytest.raises(DBAPIError, match="immutable"), factory() as session, session.begin():
        session.execute(update(PspEvent).where(PspEvent.id == event.id).values(payload_bytes=b"{}"))
    with pytest.raises(DBAPIError, match="immutable"), factory() as session, session.begin():
        session.delete(session.get(PspEvent, event.id))


def test_concurrent_duplicate_deliveries_store_one_event(factory: sessionmaker[Session]) -> None:
    _, operation = _operation(factory, "psp-concurrent")
    headers, body = signed_event(
        {"stableKey": operation.stable_provider_key, "outcome": "SUCCEEDED"}
    )
    barrier = Barrier(8)

    def deliver(_: int) -> bool:
        barrier.wait()
        return _accept(factory, headers, body)[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        replays = list(pool.map(deliver, range(8)))

    assert replays.count(False) == 1
    assert _count(factory, headers["X-Provider-Event-Id"]) == 1


def test_http_route_accepts_and_replays(factory: sessionmaker[Session]) -> None:
    _, operation = _operation(factory, "psp-http")
    headers, body = signed_event(
        {"stableKey": operation.stable_provider_key, "outcome": "SUCCEEDED"}
    )
    with TestClient(create_app()) as client:
        first = client.post("/api/inbound/v1/psp/simulator", content=body, headers=headers)
        again = client.post("/api/inbound/v1/psp/simulator", content=body, headers=headers)
        unknown = client.post("/api/inbound/v1/psp/stripe", content=body, headers=headers)

    assert (first.status_code, first.json()["replayed"], first.json()["status"]) == (
        200,
        False,
        "PENDING",
    )
    assert (again.status_code, again.json()["replayed"]) == (200, True)
    assert unknown.status_code == 404
