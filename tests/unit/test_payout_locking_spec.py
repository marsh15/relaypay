"""Red tests for the three hand-written payout reservers, written from the spec.

Every test here is ``handwritten``: it fails (``NotImplementedError``) until the maintainer
implements the strategy, and is excluded from default runs (``make handwritten``). They need
the Compose PostgreSQL, so they skip when it is down; the optimistic and constraint-guard
specs also skip, with a reason, until the schema they depend on exists (M19 ``balance_version``
column, M16 deferred reservation trigger).
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from relaypay.payouts.locking.constraint_guard import ConstraintGuardReserver
from relaypay.payouts.locking.optimistic import OptimisticReserver
from relaypay.payouts.locking.pessimistic import PessimisticReserver
from relaypay.payouts.locking.types import (
    PayoutReserver,
    ReserveOutcome,
    ReserveRequest,
    ReserveStatus,
)
from relaypay.payouts.models import Beneficiary
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres, run_in_transaction
from tests.support.fake_provider import FakeProvider
from tests.support.world import World, balances, build_world, fund_available, make_beneficiary

pytestmark = [pytest.mark.handwritten, pytest.mark.integration]

FUNDED = 50_000


def _has_balance_version(session: Session) -> bool:
    return bool(
        session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'merchant_accounts' AND column_name = 'balance_version')"
            )
        )
    )


def _has_reservation_guard_trigger(session: Session) -> bool:
    return bool(
        session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE "
                "tgrelid = 'payout_reservation_history'::regclass "
                "AND tgconstraint <> 0 AND tgdeferrable)"
            )
        )
    )


def _skip_unless(
    factory: sessionmaker[Session], probe: Callable[[Session], bool], reason: str
) -> None:
    if not run_in_transaction(factory, probe):
        pytest.skip(reason)


@dataclass(frozen=True, slots=True)
class Fixture:
    factory: sessionmaker[Session]
    world: World
    beneficiary_id: uuid.UUID

    def request(self, amount: int, key: str | None = None) -> ReserveRequest:
        return ReserveRequest(
            organisation_id=self.world.organisation_id,
            environment_id=self.world.environment_id,
            merchant_account_id=self.world.merchant_account_id,
            beneficiary_id=self.beneficiary_id,
            amount=amount,
            idempotency_key=key or f"reserve-{uuid.uuid4().hex}",
            principal=self.world.admin_principal,
        )


@pytest.fixture
def funded() -> Iterator[Fixture]:
    require_postgres()
    with relaypay_factory("payout-locking-spec") as factory:
        world = build_world(factory, label="locking-spec")
        fund_available(factory, world, FakeProvider(), FUNDED)
        public_id = make_beneficiary(factory, world)
        beneficiary_id = run_in_transaction(
            factory,
            lambda session: session.scalar(
                select(Beneficiary.id).where(Beneficiary.public_id == public_id)
            ),
        )
        assert beneficiary_id is not None
        yield Fixture(factory, world, beneficiary_id)


STRATEGIES: list[tuple[type[PayoutReserver], Callable[[sessionmaker[Session]], None]]] = [
    (PessimisticReserver, lambda factory: None),
    (
        OptimisticReserver,
        lambda factory: _skip_unless(
            factory,
            _has_balance_version,
            "merchant_accounts.balance_version does not exist yet (M19 migration 0026)",
        ),
    ),
    (
        ConstraintGuardReserver,
        lambda factory: _skip_unless(
            factory,
            _has_reservation_guard_trigger,
            "deferred reservation trigger does not exist yet (M16 migration 0022)",
        ),
    ),
]


@pytest.fixture(params=STRATEGIES, ids=lambda item: item[0].__name__)
def reserver(funded: Fixture, request: pytest.FixtureRequest) -> PayoutReserver:
    strategy, require_schema = request.param
    require_schema(funded.factory)
    return strategy()


def test_reserve_within_available_is_reserved(funded: Fixture, reserver: PayoutReserver) -> None:
    outcome = reserver.reserve(funded.factory, funded.request(20_000))
    assert outcome.status is ReserveStatus.RESERVED
    assert outcome.payout_id is not None
    assert outcome.attempts >= 1
    after = balances(funded.factory, funded.world)
    assert (after.available, after.reserved, after.payout_eligible) == (FUNDED, 20_000, 30_000)


def test_reserve_the_whole_available_balance_is_reserved(
    funded: Fixture, reserver: PayoutReserver
) -> None:
    outcome = reserver.reserve(funded.factory, funded.request(FUNDED))
    assert outcome.status is ReserveStatus.RESERVED
    assert balances(funded.factory, funded.world).payout_eligible == 0


def test_reserve_above_available_is_insufficient(funded: Fixture, reserver: PayoutReserver) -> None:
    outcome = reserver.reserve(funded.factory, funded.request(FUNDED + 1))
    assert outcome == ReserveOutcome(ReserveStatus.INSUFFICIENT, None, outcome.attempts)
    after = balances(funded.factory, funded.world)
    assert (after.reserved, after.payout_eligible) == (0, FUNDED)


def test_second_reserve_that_no_longer_fits_is_insufficient(
    funded: Fixture, reserver: PayoutReserver
) -> None:
    assert reserver.reserve(funded.factory, funded.request(40_000)).status is (
        ReserveStatus.RESERVED
    )
    outcome = reserver.reserve(funded.factory, funded.request(20_000))
    assert outcome.status is ReserveStatus.INSUFFICIENT
    assert balances(funded.factory, funded.world).reserved == 40_000


def test_same_idempotency_key_replays_the_original_payout(
    funded: Fixture, reserver: PayoutReserver
) -> None:
    request = funded.request(10_000, key=f"replay-{uuid.uuid4().hex}")
    first = reserver.reserve(funded.factory, request)
    second = reserver.reserve(funded.factory, request)
    assert first.status is ReserveStatus.RESERVED
    assert second.status is ReserveStatus.REPLAYED
    assert second.payout_id == first.payout_id
    assert balances(funded.factory, funded.world).reserved == 10_000
