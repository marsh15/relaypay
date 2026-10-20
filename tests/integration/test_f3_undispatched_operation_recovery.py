"""F3: an operation the API committed but never dispatched is first-sent by the poller.

Initiate commits the operation, its resource and the idempotency key, then the route dispatches
inline. If the API process dies in between, the operation sits PROCESSING with no
``last_sent_at``. ``claim_due_operations`` requires ``last_sent_at``, so before the fix nothing
recovered it unless the client happened to retry with the same key.

These tests assert convergence (the operation finalizes exactly once), exactly one provider
mutation per stable key, that the grace period is respected, and that the poller racing the
API's own late inline dispatch still sends exactly once.
"""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from relaypay.ledger.models import Journal
from relaypay.provider_operations.models import ProviderOperation
from relaypay.provider_operations.recovery import (
    claim_unsent_operations,
    dispatch_unsent_operations,
    run_recovery_batch,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import (
    World,
    authorize,
    build_world,
    capture,
    create_payment,
    dispatch,
    operation_status,
)

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("f3-undispatched-operation-tests", pool_size=12) as session_factory:
        yield session_factory


def _stable_key(factory: sessionmaker[Session], world: World, operation_id: str) -> str:
    with factory() as session, session.begin():
        key = session.scalar(
            select(ProviderOperation.stable_provider_key).where(
                ProviderOperation.organisation_id == world.organisation_id,
                ProviderOperation.public_id == operation_id,
            )
        )
    assert key is not None
    return str(key)


def _sweep(factory: sessionmaker[Session], world: World, provider: FakeProvider) -> int:
    return dispatch_unsent_operations(
        factory,
        provider_account_id=world.provider_account_id,
        provider_signing_secret=world.provider_signing_secret,
        transport=provider,
        grace_seconds=0,
        organisation_id=world.organisation_id,
    )


def test_undispatched_authorization_is_first_sent_by_the_poller(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="f3-authorize")
    provider = FakeProvider()
    payment = create_payment(factory, world)
    operation = authorize(factory, world, payment)  # committed, never dispatched
    assert operation_status(factory, world, operation) == "PROCESSING"

    assert _sweep(factory, world, provider) == 1

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    key = _stable_key(factory, world, operation)
    assert provider.mutation_count(key) == 1
    provider.assert_exactly_once()
    assert _sweep(factory, world, provider) == 0  # terminal: never claimed again


def test_undispatched_capture_posts_exactly_one_journal(factory: sessionmaker[Session]) -> None:
    world = build_world(factory, label="f3-capture")
    provider = FakeProvider()
    payment = create_payment(factory, world)
    authorize(factory, world, payment, provider)
    operation = capture(factory, world, payment)

    assert _sweep(factory, world, provider) == 1

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    provider.assert_exactly_once()
    with factory() as session, session.begin():
        journals = session.scalar(
            select(func.count())
            .select_from(Journal)
            .join(ProviderOperation, ProviderOperation.id == Journal.provider_operation_id)
            .where(
                ProviderOperation.organisation_id == world.organisation_id,
                ProviderOperation.public_id == operation,
            )
        )
    assert journals == 1


def test_grace_period_leaves_fresh_operations_to_the_inline_dispatcher(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="f3-grace")
    payment = create_payment(factory, world)
    authorize(factory, world, payment)

    claims = claim_unsent_operations(
        factory, grace_seconds=3600, organisation_id=world.organisation_id
    )

    assert claims == []


def test_recovery_batch_includes_unsent_operations(factory: sessionmaker[Session]) -> None:
    world = build_world(factory, label="f3-batch")
    provider = FakeProvider()
    payment = create_payment(factory, world)
    operation = authorize(factory, world, payment)
    with factory() as session, session.begin():
        stored = session.scalar(
            select(ProviderOperation).where(
                ProviderOperation.organisation_id == world.organisation_id,
                ProviderOperation.public_id == operation,
            )
        )
        assert stored is not None
        stored.created_at = stored.created_at.replace(year=stored.created_at.year - 1)

    run_recovery_batch(
        factory,
        provider_account_id=world.provider_account_id,
        provider_signing_secret=world.provider_signing_secret,
        transport=provider,
        organisation_id=world.organisation_id,
    )

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    provider.assert_exactly_once()


@pytest.mark.parametrize("round_", range(5))
def test_poller_racing_late_inline_dispatch_sends_exactly_once(
    factory: sessionmaker[Session], round_: int
) -> None:
    world = build_world(factory, label=f"f3-race-{round_}")
    provider = FakeProvider()
    payment = create_payment(factory, world)
    operation = authorize(factory, world, payment)
    barrier = threading.Barrier(2)

    def inline() -> None:
        barrier.wait()
        dispatch(factory, world, operation, provider)

    def poller() -> None:
        barrier.wait()
        _sweep(factory, world, provider)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(inline), pool.submit(poller)]:
            future.result()

    assert operation_status(factory, world, operation) == "SUCCEEDED"
    key = _stable_key(factory, world, operation)
    assert provider.mutation_count(key) == 1
    provider.assert_exactly_once()
