"""F4 / ADR-011: a signed "not found" past the grace period fails the operation.

A crash between committing "SENT" and the HTTP call means the provider (or bank) never saw the
request. Lookups answer "not found" forever, so before ADR-011 every such operation ended in
REQUIRES_REVIEW for a human. Now a signed, identity-bound not-found received at least
``not_found_grace_seconds`` after the recorded send is proof of absence:

* within the grace period the operation keeps polling and does not count toward review;
* after it, the operation fails with ``PROVIDER_EFFECT_NOT_FOUND`` (payouts: the reservation is
  released) and nothing is ever sent;
* an effect that exists always wins: not-found never overrides a real provider effect.
"""

import json
import random
from collections.abc import Iterator
from datetime import timedelta

import pytest
from relaypay.mock_bank.models import BankEffect
from relaypay.mock_provider.models import ProviderAccount
from relaypay.mock_provider.service import lookup_effect
from relaypay.payments.models import Authorization, PaymentIntent, Refund
from relaypay.payouts.models import Payout, PayoutAttempt
from relaypay.payouts.service import dispatch_payout_attempt, run_payout_batch
from relaypay.proof.crashpoints import SimulatedCrash, crash_plan
from relaypay.provider_operations.models import ProviderOperation
from relaypay.provider_operations.recovery import claim_specific_operation, recover_claim
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from scripts.chaos.driver import BANK_ACCOUNT, BANK_SECRET, LocalBankTransport, bank_factory
from tests.support.db import provider_factory, relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import (
    World,
    authorize,
    balances,
    build_world,
    create_payment,
    dispatch,
    fund_available,
    make_beneficiary,
    operation_status,
    paid_payment,
    refund,
    request_payout,
)

pytestmark = [pytest.mark.integration]

GRACE = 600


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("f4-signed-not-found-tests", pool_size=8) as session_factory:
        yield session_factory


@pytest.fixture
def bank() -> Iterator[sessionmaker[Session]]:
    with bank_factory(pool_size=4) as session_factory:
        yield session_factory


def _crash_before_send(factory: sessionmaker[Session], world: World, operation: str) -> None:
    """Dispatch dies right after committing SENT: the provider never sees the request."""
    provider = FakeProvider()
    with (
        crash_plan({"provider.after_prepare_send": 1.0}, random.Random(0)) as crashes,  # noqa: S311
        pytest.raises(SimulatedCrash),
    ):
        dispatch(factory, world, operation, provider)
    assert crashes.fired == ("provider.after_prepare_send",)
    assert provider.mutation_count() == 0


def _lookup(
    factory: sessionmaker[Session],
    world: World,
    operation: str,
    provider: FakeProvider,
    *,
    grace: int,
) -> None:
    claim = claim_specific_operation(
        factory, organisation_id=world.organisation_id, operation_public_id=operation
    )
    recover_claim(
        factory,
        claim=claim,
        provider_account_id=world.provider_account_id,
        provider_signing_secret=world.provider_signing_secret,
        transport=provider,
        not_found_grace_seconds=grace,
    )


def _operation(factory: sessionmaker[Session], world: World, operation: str) -> ProviderOperation:
    with factory() as session, session.begin():
        row = session.scalar(
            select(ProviderOperation).where(
                ProviderOperation.organisation_id == world.organisation_id,
                ProviderOperation.public_id == operation,
            )
        )
        assert row is not None
        session.expunge(row)
        return row


def test_signed_not_found_within_grace_keeps_polling_without_review(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="f4-within-grace")
    payment = create_payment(factory, world)
    operation = authorize(factory, world, payment)
    _crash_before_send(factory, world, operation)
    provider = FakeProvider()

    for _ in range(8):  # beyond the 5-lookup indeterminate limit
        _lookup(factory, world, operation, provider, grace=GRACE)

    row = _operation(factory, world, operation)
    assert row.status == "PROCESSING"
    assert row.last_sent_at is not None and row.next_lookup_at is not None
    assert row.next_lookup_at <= row.last_sent_at + timedelta(seconds=GRACE)
    assert provider.mutation_count() == 0


def test_signed_not_found_after_grace_fails_the_operation(factory: sessionmaker[Session]) -> None:
    world = build_world(factory, label="f4-after-grace")
    payment = create_payment(factory, world)
    operation = authorize(factory, world, payment)
    _crash_before_send(factory, world, operation)
    provider = FakeProvider()

    _lookup(factory, world, operation, provider, grace=0)

    assert operation_status(factory, world, operation) == "FAILED"
    with factory() as session, session.begin():
        authorization = session.scalar(
            select(Authorization)
            .join(PaymentIntent, PaymentIntent.id == Authorization.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
        )
        status = session.scalar(
            select(PaymentIntent.status).where(PaymentIntent.public_id == payment)
        )
        assert authorization is not None
        assert (authorization.status, authorization.failure_code) == (
            "FAILED",
            "PROVIDER_EFFECT_NOT_FOUND",
        )
    assert status == "failed"
    assert provider.mutation_count() == 0


def test_review_operation_is_cleared_by_admin_lookup_after_grace(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="f4-review-backlog")
    payment = create_payment(factory, world)
    operation = authorize(factory, world, payment)
    _crash_before_send(factory, world, operation)
    unsigned = FakeProvider(signing_secret="not-the-provider-secret")
    for _ in range(6):  # unverifiable not-found keeps the old path: review after 5 lookups
        _lookup(factory, world, operation, unsigned, grace=0)
    assert operation_status(factory, world, operation) == "REQUIRES_REVIEW"

    claim = claim_specific_operation(
        factory,
        organisation_id=world.organisation_id,
        operation_public_id=operation,
        require_review=True,
    )
    recover_claim(
        factory,
        claim=claim,
        provider_account_id=world.provider_account_id,
        provider_signing_secret=world.provider_signing_secret,
        transport=FakeProvider(),
        actor_type="ADMIN_LOOKUP",
        not_found_grace_seconds=0,
    )

    assert operation_status(factory, world, operation) == "FAILED"


def test_refund_never_sent_fails_and_frees_the_refundable_amount(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="f4-refund")
    provider = FakeProvider()
    paid = paid_payment(factory, world, provider, amount=100_000)
    operation = refund(factory, world, paid.payment_id, 40_000)
    _crash_before_send(factory, world, operation)

    _lookup(factory, world, operation, provider, grace=0)

    assert operation_status(factory, world, operation) == "FAILED"
    with factory() as session, session.begin():
        refund_row = session.scalar(
            select(Refund)
            .join(ProviderOperation, ProviderOperation.resource_id == Refund.id)
            .where(ProviderOperation.public_id == operation)
        )
        status = session.scalar(
            select(PaymentIntent.status).where(PaymentIntent.public_id == paid.payment_id)
        )
        assert refund_row is not None and refund_row.failure_code == "PROVIDER_EFFECT_NOT_FOUND"
    assert status == "captured"
    # The whole capture is refundable again.
    second = refund(factory, world, paid.payment_id, 100_000, provider)
    assert operation_status(factory, world, second) == "SUCCEEDED"


def test_existing_effect_always_wins_over_not_found(factory: sessionmaker[Session]) -> None:
    world = build_world(factory, label="f4-effect-wins")
    payment = create_payment(factory, world)
    provider = FakeProvider()
    operation = authorize(factory, world, payment, provider)  # effect applied
    assert operation_status(factory, world, operation) == "SUCCEEDED"
    provider.assert_exactly_once()


def test_simulator_answers_unknown_effects_with_signed_not_found() -> None:
    require_postgres()
    with provider_factory("f4-provider-sim", pool_size=2) as factory:
        with factory() as session, session.begin():
            account = session.scalar(select(ProviderAccount.public_id).limit(1))
        if account is None:
            pytest.skip("no seeded provider account")
        reply = lookup_effect(
            factory,
            account_public_id=account,
            stable_key="authorize:never-sent",
            signing_secret="sim-secret",
        )
    assert reply.status_code == 404
    assert json.loads(reply.body) == {
        "accountId": account,
        "outcome": "NOT_FOUND",
        "stableKey": "authorize:never-sent",
    }
    assert reply.headers["X-Provider-Signature"]


def _payout_never_sent(
    factory: sessionmaker[Session], bank: sessionmaker[Session]
) -> tuple[World, str]:
    world = build_world(factory, label="f4-payout")
    fund_available(factory, world, FakeProvider(), 100_000)
    beneficiary = make_beneficiary(factory, world)
    created = request_payout(factory, world, beneficiary, 60_000)
    payout_id = str(json.loads(created.body)["id"])
    with (
        crash_plan({"payout.after_prepare_send": 1.0}, random.Random(0)),  # noqa: S311
        pytest.raises(SimulatedCrash),
    ):
        dispatch_payout_attempt(
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=LocalBankTransport(bank),
        )
    return world, payout_id


def _sweep(
    factory: sessionmaker[Session], bank: sessionmaker[Session], world: World, *, grace: int
) -> None:
    run_payout_batch(
        factory,
        bank_account_id=BANK_ACCOUNT,
        bank_signing_secret=BANK_SECRET,
        transport=LocalBankTransport(bank),
        organisation_id=world.organisation_id,
        sent_grace_seconds=0,
        sent_lease_seconds=0,
        not_found_grace_seconds=grace,
    )


def _payout_states(factory: sessionmaker[Session], payout_id: str) -> tuple[str, str, str | None]:
    with factory() as session, session.begin():
        row = session.execute(
            select(Payout.status, PayoutAttempt.status, Payout.failure_code)
            .join(PayoutAttempt, PayoutAttempt.payout_id == Payout.id)
            .where(Payout.public_id == payout_id)
            .order_by(PayoutAttempt.attempt_number.desc())
            .limit(1)
        ).one()
    return str(row[0]), str(row[1]), row[2]


def test_payout_never_sent_fails_and_releases_reservation_after_grace(
    factory: sessionmaker[Session], bank: sessionmaker[Session]
) -> None:
    world, payout_id = _payout_never_sent(factory, bank)
    assert balances(factory, world).reserved == 60_000

    _sweep(factory, bank, world, grace=GRACE)  # within grace: held, not failed
    status, attempt, _ = _payout_states(factory, payout_id)
    assert status != "FAILED" and attempt == "AMBIGUOUS"
    assert balances(factory, world).reserved == 60_000

    _sweep(factory, bank, world, grace=0)

    assert _payout_states(factory, payout_id) == ("FAILED", "FAILED", "BANK_EFFECT_NOT_FOUND")
    assert balances(factory, world).reserved == 0
    with bank() as session, session.begin():
        effects = session.scalar(
            select(func.count())
            .select_from(BankEffect)
            .where(BankEffect.payout_reference == payout_id)
        )
    assert effects == 0
