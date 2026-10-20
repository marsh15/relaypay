"""M16: void releases an authorization hold; void and capture exclude each other."""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from relaypay.errors import RelayPayError
from relaypay.ledger.models import Posting
from relaypay.payments.expiry import expire_authorizations
from relaypay.payments.models import Authorization, PaymentIntent, Void
from relaypay.provider_operations.models import ProviderOperation
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.world import (
    World,
    authorize,
    build_world,
    capture,
    create_payment,
    dispatch,
    operation_status,
    recover,
    void,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("void-tests", pool_size=8) as session_factory:
        yield session_factory


def _authorized(factory: sessionmaker[Session], label: str) -> tuple[World, str, FakeProvider]:
    world = build_world(factory, label=label)
    provider = FakeProvider()
    payment = create_payment(factory, world)
    authorize(factory, world, payment, provider)
    return world, payment, provider


def _status(factory: sessionmaker[Session], payment: str) -> str:
    with factory() as session, session.begin():
        return str(
            session.scalar(select(PaymentIntent.status).where(PaymentIntent.public_id == payment))
        )


def _stable_key(factory: sessionmaker[Session], operation: str) -> str:
    with factory() as session, session.begin():
        key = session.scalar(
            select(ProviderOperation.stable_provider_key).where(
                ProviderOperation.public_id == operation
            )
        )
    assert key is not None
    return str(key)


def _postings(factory: sessionmaker[Session], world: World) -> int:
    with factory() as session, session.begin():
        return int(
            session.scalar(
                select(func.count())
                .select_from(Posting)
                .where(Posting.organisation_id == world.organisation_id)
            )
            or 0
        )


def test_void_cancels_an_authorised_payment_without_posting(
    factory: sessionmaker[Session],
) -> None:
    world, payment, provider = _authorized(factory, "void-happy")
    postings_before = _postings(factory, world)

    operation = void(factory, world, payment, provider)

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    assert _status(factory, payment) == "cancelled"
    assert _stable_key(factory, operation).startswith("void:")
    with factory() as session, session.begin():
        row = session.scalar(
            select(Void)
            .join(PaymentIntent, PaymentIntent.id == Void.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
        )
        assert row is not None
        assert (row.status, row.amount, row.failure_code) == ("SUCCEEDED", 100_000, None)
        assert row.voided_at is not None
    assert _postings(factory, world) == postings_before
    provider.assert_exactly_once()


def test_capture_is_refused_once_a_void_exists(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "void-then-capture")
    operation = void(factory, world, payment)  # in flight

    with pytest.raises(RelayPayError) as in_flight:
        capture(factory, world, payment)
    assert in_flight.value.code == "VOID_IN_PROGRESS"

    dispatch(factory, world, operation, provider)
    with pytest.raises(RelayPayError) as done:
        capture(factory, world, payment)
    assert done.value.code == "VOID_IN_PROGRESS"


def test_void_is_refused_once_a_capture_exists(factory: sessionmaker[Session]) -> None:
    world, payment, _ = _authorized(factory, "capture-then-void")
    capture(factory, world, payment)  # in flight

    with pytest.raises(RelayPayError) as raised:
        void(factory, world, payment)

    assert raised.value.code == "PAYMENT_NOT_VOIDABLE"


def test_void_requires_a_succeeded_authorization(factory: sessionmaker[Session]) -> None:
    world = build_world(factory, label="void-unauthorised")
    payment = create_payment(factory, world)

    with pytest.raises(RelayPayError) as raised:
        void(factory, world, payment)

    assert raised.value.code == "VOID_REQUIRES_SUCCEEDED_AUTHORIZATION"


def test_declined_void_leaves_the_payment_capturable(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "void-declined")
    operation = void(factory, world, payment)
    provider.script(_stable_key(factory, operation), Outcome.DECLINE)

    dispatch(factory, world, operation, provider)

    assert operation_status(factory, world, operation) == "FAILED"
    assert _status(factory, payment) == "authorised"
    captured = capture(factory, world, payment, provider)
    assert operation_status(factory, world, captured) == "SUCCEEDED"
    assert _status(factory, payment) == "captured"


def test_void_replays_and_converges_like_every_operation(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "void-replay")
    key = "void-replay-key"
    first = void(factory, world, payment, provider, key=key)

    assert void(factory, world, payment, key=key) == first  # same key replays
    assert void(factory, world, payment) == first  # a new key converges on the one void
    provider.assert_exactly_once()


def test_lost_void_response_recovers_by_lookup(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "void-lost")
    operation = void(factory, world, payment)
    key = _stable_key(factory, operation)
    provider.script(key, Outcome.LOSE_RESPONSE)

    dispatch(factory, world, operation, provider)
    assert operation_status(factory, world, operation) == "PROCESSING"
    recover(factory, world, operation, provider)

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    assert _status(factory, payment) == "cancelled"
    assert provider.mutation_count(key) == 1


def test_expiry_skips_a_payment_whose_void_is_in_flight(factory: sessionmaker[Session]) -> None:
    world, payment, provider = _authorized(factory, "void-vs-expiry")
    operation = void(factory, world, payment)
    with factory() as session, session.begin():
        session.execute(
            update(Authorization)
            .where(Authorization.organisation_id == world.organisation_id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )

    assert expire_authorizations(factory, organisation_id=world.organisation_id) == 0

    dispatch(factory, world, operation, provider)
    assert _status(factory, payment) == "cancelled"


@pytest.mark.parametrize("round_", range(6))
def test_void_racing_capture_initiates_exactly_one(
    factory: sessionmaker[Session], round_: int
) -> None:
    world, payment, _ = _authorized(factory, f"void-vs-capture-{round_}")
    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def run(name: str, command: Callable[[], str]) -> None:
        barrier.wait()
        try:
            command()
            outcomes[name] = "accepted"
        except RelayPayError as error:
            outcomes[name] = error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(run, "void", lambda: void(factory, world, payment)),
            pool.submit(run, "capture", lambda: capture(factory, world, payment)),
        ]
        for future in futures:
            future.result()

    accepted = [name for name, result in outcomes.items() if result == "accepted"]
    assert len(accepted) == 1, outcomes
    loser = "capture" if accepted == ["void"] else "void"
    assert outcomes[loser] in {"VOID_IN_PROGRESS", "PAYMENT_NOT_VOIDABLE"}, outcomes
