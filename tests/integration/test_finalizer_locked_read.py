"""The finalizer's locked read must see the status committed while it waited for the lock.

The chaos invariant checker found operations with two PROCESSING -> FAILED history rows: a second
finalizer had loaded the operation before blocking on its row lock and, without refreshing,
finalized it again. This reproduces that interleaving deterministically.
"""

from collections.abc import Iterator

import pytest
from relaypay.provider_operations.finalizer import _locked_graph
from relaypay.provider_operations.models import OperationHistory, ProviderOperation
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.world import (
    authorize,
    build_world,
    create_payment,
    dispatch,
    operation_status,
    recover,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("finalizer-locked-read", pool_size=4) as session_factory:
        yield session_factory


def test_locked_read_refreshes_a_status_finalized_after_the_first_load(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="finalizer-locked-read")
    payment_id = create_payment(factory, world)
    operation_id = authorize(factory, world, payment_id)
    provider = FakeProvider()
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None
        key, internal_id = operation.stable_provider_key, operation.id
    provider.script(key, Outcome.LOSE_RESPONSE_DECLINE)
    dispatch(factory, world, operation_id, provider)
    assert operation_status(factory, world, operation_id) == "PROCESSING"

    with factory() as stale, stale.begin():
        loaded = stale.get(ProviderOperation, internal_id)  # the unlocked first read
        assert loaded is not None
        assert loaded.status == "PROCESSING"

        recover(factory, world, operation_id, provider)  # another worker finalizes meanwhile
        assert operation_status(factory, world, operation_id) == "FAILED"

        _, operation, _, _ = _locked_graph(stale, internal_id)
        assert operation.status == "FAILED"

    with factory() as session, session.begin():
        rows = session.scalar(
            select(func.count(OperationHistory.id)).where(
                OperationHistory.provider_operation_id == internal_id
            )
        )
    assert rows == 1
