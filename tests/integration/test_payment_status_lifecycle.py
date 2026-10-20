"""Real flows through the real services (only the provider is faked) walk the status machine.

After every flow the history must be a legal walk: it starts with the version-0 creation row,
each row continues from the previous row's target, every step is in ``ALLOWED``, it ends at the
payment's current status, and ``status_version`` equals the number of history rows minus that
initial row.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise

import pytest
from relaypay.payments.models import PaymentIntent, PaymentStatusHistory
from relaypay.payments.transitions import ALLOWED
from sqlalchemy import select
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
    refund,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Walk:
    status: str
    version: int
    steps: list[tuple[str | None, str, str, int]]  # (from, to, reason, version)

    @property
    def path(self) -> list[str]:
        return [self.steps[0][1], *(step[1] for step in self.steps[1:])]


@pytest.fixture(scope="module")
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("payment-status-lifecycle-tests") as session_factory:
        yield session_factory


@pytest.fixture
def world(factory: sessionmaker[Session]) -> World:
    return build_world(factory, label="status-lifecycle")


def walk(factory: sessionmaker[Session], world: World, payment_id: str) -> Walk:
    """Read status and history, asserting the walk invariants on the way."""
    with factory() as session, session.begin():
        payment = session.scalar(
            select(PaymentIntent).where(
                PaymentIntent.organisation_id == world.organisation_id,
                PaymentIntent.public_id == payment_id,
            )
        )
        assert payment is not None
        history = list(
            session.scalars(
                select(PaymentStatusHistory)
                .where(PaymentStatusHistory.payment_intent_id == payment.id)
                .order_by(PaymentStatusHistory.status_version)
            )
        )
        steps = [(h.from_status, h.to_status, h.reason, h.status_version) for h in history]
        status, version = payment.status, payment.status_version
    assert steps, "every payment has an initial history row"
    first = steps[0]
    assert first[0] is None and first[1] == "created" and first[3] == 0
    assert first[2] == "PAYMENT_CREATED"
    for previous, current in pairwise(steps):
        assert current[0] == previous[1], "history is a connected walk"
        assert (current[0], current[1]) in ALLOWED
        assert current[3] == previous[3] + 1, "versions are contiguous"
    assert steps[-1][1] == status, "history ends at the current status"
    assert version == len(steps) - 1, "status_version == history rows minus the initial row"
    return Walk(status, version, steps)


def test_new_payment_is_created_with_one_history_row(
    factory: sessionmaker[Session], world: World
) -> None:
    payment_id = create_payment(factory, world)
    result = walk(factory, world, payment_id)
    assert (result.status, result.version) == ("created", 0)
    assert len(result.steps) == 1


def test_succeed_capture_and_refunds_walk_to_refunded(
    factory: sessionmaker[Session], world: World
) -> None:
    provider = FakeProvider()
    payment_id = create_payment(factory, world, amount=10_000)

    authorize(factory, world, payment_id, provider)
    assert walk(factory, world, payment_id).status == "authorised"
    capture(factory, world, payment_id, provider)
    assert walk(factory, world, payment_id).status == "captured"

    refund(factory, world, payment_id, 3_000, provider)
    assert walk(factory, world, payment_id).status == "partially_refunded"
    refund(factory, world, payment_id, 2_000, provider)
    assert walk(factory, world, payment_id).status == "partially_refunded"
    refund(factory, world, payment_id, 5_000, provider)

    final = walk(factory, world, payment_id)
    assert final.status == "refunded"
    assert final.path == [
        "created",
        "authorised",
        "captured",
        "partially_refunded",
        "partially_refunded",
        "refunded",
    ]
    assert final.version == 5
    assert [step[2] for step in final.steps[1:]] == [
        "AUTHORIZATION_SUCCEEDED",
        "CAPTURE_SUCCEEDED",
        "REFUND_SUCCEEDED",
        "REFUND_SUCCEEDED",
        "REFUND_SUCCEEDED",
    ]


def test_full_refund_in_one_step(factory: sessionmaker[Session], world: World) -> None:
    provider = FakeProvider()
    payment_id = create_payment(factory, world, amount=7_000)
    authorize(factory, world, payment_id, provider)
    capture(factory, world, payment_id, provider)
    refund(factory, world, payment_id, 7_000, provider)
    final = walk(factory, world, payment_id)
    assert final.path == ["created", "authorised", "captured", "refunded"]


def test_authorization_decline_fails_the_payment(
    factory: sessionmaker[Session], world: World
) -> None:
    provider = FakeProvider(default=Outcome.DECLINE)
    payment_id = create_payment(factory, world)
    authorize(factory, world, payment_id, provider)
    final = walk(factory, world, payment_id)
    assert final.path == ["created", "failed"]
    assert final.steps[-1][2] == "AUTHORIZATION_DECLINED"


def test_capture_decline_fails_the_payment(factory: sessionmaker[Session], world: World) -> None:
    provider = FakeProvider()
    payment_id = create_payment(factory, world)
    authorize(factory, world, payment_id, provider)
    provider.default = Outcome.DECLINE
    capture_op = capture(factory, world, payment_id, provider)
    assert operation_status(factory, world, capture_op) == "FAILED"
    final = walk(factory, world, payment_id)
    assert final.path == ["created", "authorised", "failed"]
    assert final.steps[-1][2] == "CAPTURE_DECLINED"


def test_refund_decline_leaves_status_and_history_unchanged(
    factory: sessionmaker[Session], world: World
) -> None:
    provider = FakeProvider()
    payment_id = create_payment(factory, world, amount=5_000)
    authorize(factory, world, payment_id, provider)
    capture(factory, world, payment_id, provider)
    before = walk(factory, world, payment_id)

    provider.default = Outcome.DECLINE
    refund_op = refund(factory, world, payment_id, 1_000, provider)
    assert operation_status(factory, world, refund_op) == "FAILED"
    assert walk(factory, world, payment_id) == before
    assert before.status == "captured"

    provider.default = Outcome.SUCCEED
    refund(factory, world, payment_id, 5_000, provider)
    assert walk(factory, world, payment_id).status == "refunded"


def test_lost_response_stays_put_until_recovery_finalizes(
    factory: sessionmaker[Session], world: World
) -> None:
    provider = FakeProvider()
    payment_id = create_payment(factory, world)
    authorize_op = authorize(factory, world, payment_id)
    provider.default = Outcome.LOSE_RESPONSE
    dispatch(factory, world, authorize_op, provider)
    assert operation_status(factory, world, authorize_op) == "PROCESSING"
    assert walk(factory, world, payment_id).status == "created"

    recover(factory, world, authorize_op, provider)
    assert operation_status(factory, world, authorize_op) == "SUCCEEDED"
    assert walk(factory, world, payment_id).path == ["created", "authorised"]

    # Same recovery path for a lost capture response.
    provider.default = Outcome.LOSE_RESPONSE
    capture_op = capture(factory, world, payment_id)
    dispatch(factory, world, capture_op, provider)
    assert walk(factory, world, payment_id).status == "authorised"
    recover(factory, world, capture_op, provider)
    assert walk(factory, world, payment_id).path == ["created", "authorised", "captured"]
    provider.assert_exactly_once()


def test_lost_declined_capture_recovers_to_failed(
    factory: sessionmaker[Session], world: World
) -> None:
    provider = FakeProvider()
    payment_id = create_payment(factory, world)
    authorize(factory, world, payment_id, provider)
    provider.default = Outcome.LOSE_RESPONSE_DECLINE
    capture_op = capture(factory, world, payment_id, provider)
    assert walk(factory, world, payment_id).status == "authorised"
    recover(factory, world, capture_op, provider)
    assert walk(factory, world, payment_id).path == ["created", "authorised", "failed"]


def test_pending_authorization_resolves_through_lookup(
    factory: sessionmaker[Session], world: World
) -> None:
    provider = FakeProvider(default=Outcome.PENDING)
    payment_id = create_payment(factory, world)
    authorize_op = authorize(factory, world, payment_id, provider)
    assert walk(factory, world, payment_id).status == "created"

    (stable_key,) = provider.mutated_keys()
    provider.resolve(stable_key, Outcome.SUCCEED)
    recover(factory, world, authorize_op, provider)
    assert walk(factory, world, payment_id).path == ["created", "authorised"]
