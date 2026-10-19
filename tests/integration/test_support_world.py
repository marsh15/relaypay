"""Exercises tests/support: isolated worlds, fake provider scripting and money-path drivers."""

import uuid
from collections.abc import Iterator

import pytest
from relaypay.errors import RelayPayError
from relaypay.ledger.models import Journal, Posting
from relaypay.provider_operations.models import ProviderOperation
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres, run_in_transaction
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.world import (
    World,
    authorize,
    balances,
    build_world,
    capture,
    create_payment,
    dispatch,
    fund_available,
    make_beneficiary,
    operation_status,
    paid_payment,
    payment_row,
    recover,
    refund,
    request_payout,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("support-world-tests") as session_factory:
        yield session_factory


def _stable_key(factory: sessionmaker[Session], world: World, operation_id: str) -> str:
    key = run_in_transaction(
        factory,
        lambda session: session.scalar(
            select(ProviderOperation.stable_provider_key).where(
                ProviderOperation.organisation_id == world.organisation_id,
                ProviderOperation.public_id == operation_id,
            )
        ),
    )
    assert key is not None
    return str(key)


def _journals_balanced(factory: sessionmaker[Session], world: World) -> int:
    def check(session: Session) -> int:
        journals = list(
            session.scalars(select(Journal).where(Journal.organisation_id == world.organisation_id))
        )
        for journal in journals:
            postings = list(
                session.scalars(select(Posting).where(Posting.journal_id == journal.id))
            )
            assert len(postings) >= 2
            assert sum(p.amount for p in postings if p.side == "DEBIT") == sum(
                p.amount for p in postings if p.side == "CREDIT"
            )
        return len(journals)

    return run_in_transaction(factory, check)


def test_two_worlds_capture_and_refund_with_one_provider_effect_per_key(
    factory: sessionmaker[Session],
) -> None:
    world_a = build_world(factory, label="support-a")
    world_b = build_world(factory, label="support-b")
    assert world_a.organisation_id != world_b.organisation_id
    assert world_a.merchant_account_id != world_b.merchant_account_id
    assert world_a.customer_public_id != world_b.customer_public_id
    assert world_a.api_key != world_b.api_key

    provider = FakeProvider()
    flows = {
        id(world): paid_payment(factory, world, provider, amount=100_000)
        for world in (world_a, world_b)
    }
    refund_ops = {
        id(world): refund(factory, world, flows[id(world)].payment_id, 40_000, provider)
        for world in (world_a, world_b)
    }

    for world in (world_a, world_b):
        flow = flows[id(world)]
        for operation_id in (
            flow.authorization_operation_id,
            flow.capture_operation_id,
            refund_ops[id(world)],
        ):
            assert operation_status(factory, world, operation_id) == "SUCCEEDED"
            key = _stable_key(factory, world, operation_id)
            assert provider.mutation_count(key) == 1
            assert key in provider.effects
        # one journal per capture and per refund, each balanced
        assert _journals_balanced(factory, world) == 2
        assert payment_row(factory, world, flow.payment_id).amount == 100_000
        # 100_000 captured, 40_000 refunded out of PENDING
        assert balances(factory, world).pending == 60_000

    assert provider.mutation_count() == 6
    assert len(provider.effects) == 6
    provider.assert_exactly_once()


def test_lost_response_recovers_by_lookup_without_second_mutation(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="support-lost")
    provider = FakeProvider()
    payment_id = create_payment(factory, world)
    authorize(factory, world, payment_id, provider)
    capture_op = capture(factory, world, payment_id)
    provider.script(_stable_key(factory, world, capture_op), Outcome.LOSE_RESPONSE)

    dispatch(factory, world, capture_op, provider)
    assert operation_status(factory, world, capture_op) == "PROCESSING"
    key = _stable_key(factory, world, capture_op)
    assert provider.effects[key].outcome == "SUCCEEDED"

    recover(factory, world, capture_op, provider)
    assert operation_status(factory, world, capture_op) == "SUCCEEDED"
    assert provider.mutation_count(key) == 1
    assert provider.lookup_count(key) == 1
    assert _journals_balanced(factory, world) == 1
    provider.assert_exactly_once()


def test_decline_and_pending_scripts_drive_operations_as_scripted(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="support-scripted")
    provider = FakeProvider()
    payment_id = create_payment(factory, world)
    authorize(factory, world, payment_id, provider)
    capture_op = capture(factory, world, payment_id)
    key = _stable_key(factory, world, capture_op)

    provider.script(key, Outcome.PENDING)
    dispatch(factory, world, capture_op, provider)
    assert operation_status(factory, world, capture_op) == "PROCESSING"
    assert provider.effects[key].outcome == "PENDING"

    provider.resolve(key, Outcome.DECLINE)
    recover(factory, world, capture_op, provider)
    assert operation_status(factory, world, capture_op) == "FAILED"
    assert provider.effects[key].decline_code == "DO_NOT_HONOR"
    assert provider.mutation_count(key) == 1
    assert _journals_balanced(factory, world) == 0


def test_fund_available_then_payout_reserves_and_rejects_overdraw(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="support-payout")
    provider = FakeProvider()
    fund_available(factory, world, provider, 50_000)
    assert balances(factory, world).available == 50_000

    beneficiary_id = make_beneficiary(factory, world)
    request_payout(factory, world, beneficiary_id, 30_000, key=f"payout-{uuid.uuid4().hex}")
    after = balances(factory, world)
    assert (after.available, after.reserved, after.payout_eligible) == (50_000, 30_000, 20_000)
    with pytest.raises(RelayPayError) as overdraw:
        request_payout(factory, world, beneficiary_id, 30_000)
    assert overdraw.value.code == "INSUFFICIENT_PAYOUT_BALANCE"


def test_worlds_are_isolated_in_one_database(factory: sessionmaker[Session]) -> None:
    world_a = build_world(factory, label="support-iso-a")
    world_b = build_world(factory, label="support-iso-b")
    provider = FakeProvider()
    paid_payment(factory, world_a, provider)

    def journal_count(session: Session, world: World) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Journal)
                .where(Journal.organisation_id == world.organisation_id)
            )
            or 0
        )

    assert run_in_transaction(factory, lambda s: journal_count(s, world_a)) == 1
    assert run_in_transaction(factory, lambda s: journal_count(s, world_b)) == 0
