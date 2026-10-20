"""``capture_snapshot`` against real PostgreSQL: scoping, fidelity, determinism, read-only."""

import hashlib
import uuid
from collections.abc import Iterator

import pytest
from relaypay.ids import new_uuid
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.merchant_balances.models import BalanceTransaction
from relaypay.mock_provider.models import ProviderAccount, ProviderEffect
from relaypay.payments.models import Authorization, Capture, PaymentIntent, Refund
from relaypay.proof.snapshot import capture_snapshot
from relaypay.proof.snapshot_types import AckedOp, ProviderEffectSnap, SystemSnapshot
from relaypay.provider_operations.models import (
    IdempotencyRecord,
    OperationHistory,
    ProviderOperation,
)
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import (
    provider_factory,
    relaypay_factory,
    require_postgres,
    run_in_transaction,
)
from tests.support.fake_provider import FakeProvider
from tests.support.snapshots import fake_provider_effects
from tests.support.world import (
    World,
    balances,
    build_world,
    fund_available,
    make_beneficiary,
    paid_payment,
    refund,
    request_payout,
)

pytestmark = pytest.mark.integration

REFUND_AMOUNT = 30_000
PAYMENT_AMOUNT = 100_000
PAYOUT_AMOUNT = 10_000


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("proof-snapshot-tests") as session_factory:
        yield session_factory


@pytest.fixture
def provider_sessions() -> Iterator[sessionmaker[Session]]:
    with provider_factory("proof-snapshot-provider-tests") as session_factory:
        yield session_factory


def _drive(
    factory: sessionmaker[Session], world: World, provider: FakeProvider, *, with_payout: bool
) -> str:
    """One paid payment, partially refunded; optionally a settled balance with a reserved payout."""
    flow = paid_payment(factory, world, provider, amount=PAYMENT_AMOUNT)
    refund(factory, world, flow.payment_id, REFUND_AMOUNT, provider)
    if with_payout:
        fund_available(factory, world, provider, 50_000)
        request_payout(factory, world, make_beneficiary(factory, world), PAYOUT_AMOUNT)
    return flow.payment_id


@pytest.fixture
def two_worlds(
    factory: sessionmaker[Session],
) -> tuple[World, World, FakeProvider]:
    provider = FakeProvider()
    world_one = build_world(factory, label="snapshot-one")
    world_two = build_world(factory, label="snapshot-two")
    _drive(factory, world_one, provider, with_payout=True)
    _drive(factory, world_two, provider, with_payout=False)
    return world_one, world_two, provider


def _count(factory: sessionmaker[Session], model: type, organisation_id: uuid.UUID) -> int:
    return run_in_transaction(
        factory,
        lambda session: int(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.organisation_id == organisation_id)
            )
            or 0
        ),
    )


def _organisations(snapshot: SystemSnapshot) -> set[str]:
    return (
        {a.organisation_id for a in snapshot.ledger_accounts}
        | {j.organisation_id for j in snapshot.journals}
        | {p.organisation_id for p in snapshot.payment_intents}
        | {b.organisation_id for b in snapshot.merchant_balances}
    )


def test_scoped_snapshot_contains_only_the_selected_world(
    factory: sessionmaker[Session], two_worlds: tuple[World, World, FakeProvider]
) -> None:
    one, two, provider = two_worlds
    snapshot = capture_snapshot(
        factory,
        None,
        organisation_ids=[one.organisation_id],
        extra_provider_effects=fake_provider_effects(provider),
    )

    assert _organisations(snapshot) == {str(one.organisation_id)}
    org = one.organisation_id
    assert len(snapshot.ledger_accounts) == _count(factory, LedgerAccount, org)
    assert len(snapshot.journals) == _count(factory, Journal, org)
    assert len(snapshot.postings) == _count(factory, Posting, org)
    assert len(snapshot.balance_transactions) == _count(factory, BalanceTransaction, org)
    assert len(snapshot.payment_intents) == _count(factory, PaymentIntent, org) == 2
    assert len(snapshot.authorizations) == _count(factory, Authorization, org)
    assert len(snapshot.captures) == _count(factory, Capture, org)
    assert len(snapshot.refunds) == _count(factory, Refund, org) == 1
    assert len(snapshot.provider_operations) == _count(factory, ProviderOperation, org)
    assert len(snapshot.operation_history) == _count(factory, OperationHistory, org)
    # Payment-create records have no provider operation and are not part of the snapshot.
    assert len(snapshot.idempotency_records) == run_in_transaction(
        factory,
        lambda session: session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(
                IdempotencyRecord.organisation_id == org,
                IdempotencyRecord.provider_operation_id.is_not(None),
            )
        ),
    )

    # Every provider effect in scope belongs to a scoped operation, none of world two's.
    keys = {operation.stable_provider_key for operation in snapshot.provider_operations}
    assert {effect.stable_key for effect in snapshot.provider_effects} == keys
    assert snapshot.provider_effects
    assert str(two.organisation_id) not in _organisations(snapshot)


def test_snapshot_rows_match_what_the_services_wrote(
    factory: sessionmaker[Session], two_worlds: tuple[World, World, FakeProvider]
) -> None:
    one, _, provider = two_worlds
    snapshot = capture_snapshot(
        factory,
        None,
        organisation_ids=[one.organisation_id],
        extra_provider_effects=fake_provider_effects(provider),
    )

    # An organisation has one default merchant account per environment; only TEST is driven.
    (merchant,) = (
        b
        for b in snapshot.merchant_balances
        if b.merchant_account_id == str(one.merchant_account_id)
    )
    derived = balances(factory, one)
    assert merchant.merchant_account_id == str(one.merchant_account_id)
    assert (merchant.pending, merchant.available, merchant.receivable, merchant.reserved) == (
        derived.pending,
        derived.available,
        derived.receivable,
        derived.reserved,
    )
    assert merchant.reserved == PAYOUT_AMOUNT
    (payout,) = snapshot.payouts
    assert payout.amount == PAYOUT_AMOUNT
    assert sum(r.amount_delta for r in snapshot.payout_reservations) == PAYOUT_AMOUNT
    assert len(snapshot.settlement_items) >= 1

    # Both payments finished synchronously: every operation terminal with matching key bytes.
    assert {op.status for op in snapshot.provider_operations} == {"SUCCEEDED"}
    assert {op.kind for op in snapshot.provider_operations} == {"AUTHORIZE", "CAPTURE", "REFUND"}
    by_operation = {op.id: op for op in snapshot.provider_operations}
    for record in snapshot.idempotency_records:
        assert record.is_terminal
        assert (
            record.response_sha256
            == by_operation[record.provider_operation_id].terminal_response_sha256
        )
    for operation_id in by_operation:
        steps = [h for h in snapshot.operation_history if h.provider_operation_id == operation_id]
        assert [h.ordinal for h in steps] == list(range(len(steps)))
        assert steps[0].from_status == "PROCESSING"
        assert steps[-1].to_status == "SUCCEEDED"

    journal_ids = {journal.id for journal in snapshot.journals}
    captured = [c for c in snapshot.captures if c.status == "SUCCEEDED"]
    assert captured and all(c.journal_id in journal_ids for c in captured)
    assert all(refund_row.journal_id in journal_ids for refund_row in snapshot.refunds)
    assert {p.journal_id for p in snapshot.postings} <= journal_ids
    # Migration 0019 persists payment status; every payment here finished capture, so its
    # status follows from its SUCCEEDED refunds.
    for payment in snapshot.payment_intents:
        (capture_row,) = (c for c in captured if c.payment_intent_id == payment.id)
        refunded = sum(
            r.amount
            for r in snapshot.refunds
            if r.payment_intent_id == payment.id and r.status == "SUCCEEDED"
        )
        expected = (
            "captured"
            if refunded == 0
            else "refunded"
            if refunded == capture_row.amount
            else "partially_refunded"
        )
        assert payment.status == expected
    assert all(c.fee_amount == c.gst_amount == 0 for c in snapshot.captures)

    for effect in snapshot.provider_effects:
        operation = next(
            op for op in snapshot.provider_operations if op.stable_provider_key == effect.stable_key
        )
        assert effect.kind == operation.kind
        assert effect.status == "SUCCEEDED"


def test_unscoped_snapshot_includes_every_world_and_acked_ops(
    factory: sessionmaker[Session], two_worlds: tuple[World, World, FakeProvider]
) -> None:
    one, two, _ = two_worlds
    acked = (AckedOp("CAPTURE", "cap_example", "hint", 1),)
    snapshot = capture_snapshot(factory, None, acked_ops=acked)
    assert {str(one.organisation_id), str(two.organisation_id)} <= _organisations(snapshot)
    assert snapshot.acked_ops == acked


def test_empty_scope_yields_an_empty_snapshot(factory: sessionmaker[Session]) -> None:
    snapshot = capture_snapshot(factory, None, organisation_ids=[])
    assert snapshot == SystemSnapshot()


def test_capture_is_deterministic_and_read_only(
    factory: sessionmaker[Session], two_worlds: tuple[World, World, FakeProvider]
) -> None:
    one, _, provider = two_worlds
    models = (
        LedgerAccount,
        Journal,
        Posting,
        BalanceTransaction,
        PaymentIntent,
        Authorization,
        Capture,
        Refund,
        ProviderOperation,
        OperationHistory,
        IdempotencyRecord,
    )

    def counts() -> list[int]:
        return [_count(factory, model, one.organisation_id) for model in models]

    before = counts()
    kwargs = {
        "organisation_ids": [one.organisation_id],
        "extra_provider_effects": fake_provider_effects(provider),
    }
    first = capture_snapshot(factory, None, **kwargs)  # type: ignore[arg-type]
    second = capture_snapshot(factory, None, **kwargs)  # type: ignore[arg-type]
    assert first == second
    assert hash(first) == hash(second)
    assert counts() == before


def test_provider_database_effects_are_scoped_to_the_selected_world(
    factory: sessionmaker[Session],
    provider_sessions: sessionmaker[Session],
    two_worlds: tuple[World, World, FakeProvider],
) -> None:
    one, two, provider = two_worlds
    effects = fake_provider_effects(provider)
    account_public_id = f"acct_snapshot_{uuid.uuid4().hex}"
    account_id = new_uuid()
    with provider_sessions() as session, session.begin():
        session.add(
            ProviderAccount(
                id=account_id,
                public_id=account_public_id,
                name="snapshot test account",
                signing_secret_digest=b"unused",
            )
        )
        session.flush()
        session.add_all(_provider_rows(account_id, effects))
    try:
        scoped = capture_snapshot(
            factory, provider_sessions, organisation_ids=[one.organisation_id]
        )
        everything = capture_snapshot(factory, provider_sessions)
    finally:
        with provider_sessions() as session, session.begin():
            session.execute(
                delete(ProviderEffect).where(ProviderEffect.provider_account_id == account_id)
            )
            session.execute(delete(ProviderAccount).where(ProviderAccount.id == account_id))

    world_one_keys = {op.stable_provider_key for op in scoped.provider_operations}
    assert world_one_keys
    assert {e.stable_key for e in scoped.provider_effects} == world_one_keys
    assert {e.provider_account_id for e in scoped.provider_effects} == {account_public_id}
    both = {e.stable_key for e in effects}
    assert both <= {e.stable_key for e in everything.provider_effects}
    assert len(both) > len(world_one_keys), "world two must have contributed effects"
    assert str(two.organisation_id) in _organisations(everything)


def _provider_rows(
    account_id: uuid.UUID, effects: tuple[ProviderEffectSnap, ...]
) -> list[ProviderEffect]:
    from datetime import UTC, datetime

    return [
        ProviderEffect(
            provider_account_id=account_id,
            stable_key=effect.stable_key,
            operation_kind=effect.kind,
            reference=effect.reference,
            amount=effect.amount,
            currency="INR",
            request_sha256=hashlib.sha256(effect.stable_key.encode()).digest(),
            outcome=effect.status,
            response_bytes=b"{}",
            completed_at=datetime.now(UTC),
        )
        for effect in effects
    ]
