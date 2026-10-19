"""Property proof: partial refunds never exceed what was captured.

For every generated capture amount and list of refund requests (many deliberately larger than
what remains), applied one by one and from 8 concurrent workers:

* the sum of refunds that RelayPay accepted never exceeds the captured amount,
* every rejection is ``REFUND_AMOUNT_EXCEEDS_AVAILABLE`` (HTTP 409),
* every journal balances, and
* ``refundableAmount`` from ``read_payment`` equals captured minus the reserved refunds.
"""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st
from relaypay.errors import RelayPayError
from relaypay.ledger.models import Journal, Posting
from relaypay.payments.models import Refund
from relaypay.payments.service import read_payment
from relaypay.provider_operations.models import ProviderOperation
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.world import World, build_world, dispatch, paid_payment, recover, refund

pytestmark = [pytest.mark.integration, pytest.mark.proof, pytest.mark.concurrency]

EXCEEDS = "REFUND_AMOUNT_EXCEEDS_AVAILABLE"
RESERVING = ("PROCESSING", "REQUIRES_REVIEW", "SUCCEEDED")
WORKERS = 8


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    # Eight workers each hold a connection while locking the payment row; leave headroom.
    with relaypay_factory(
        "property-refunds-tests", pool_size=12, max_overflow=8
    ) as session_factory:
        yield session_factory


@dataclass(frozen=True)
class Attempt:
    amount: int
    accepted: bool
    error_code: str | None
    available: int | None
    operation_id: str | None


def _try_refund(
    factory: sessionmaker[Session],
    world: World,
    payment_id: str,
    amount: int,
    provider: FakeProvider,
    *,
    outcome: Outcome = Outcome.SUCCEED,
) -> Attempt:
    """Initiate one refund; on acceptance script the provider and dispatch it."""
    try:
        operation_id = refund(factory, world, payment_id, amount)
    except RelayPayError as error:
        details = error.details or {}
        assert error.http_status == 409
        return Attempt(amount, False, error.code, details.get("availableAmount"), None)
    stable_key = _stable_key(factory, world, operation_id)
    provider.script(stable_key, outcome)  # keys exist only after initiation
    dispatch(factory, world, operation_id, provider)
    return Attempt(amount, True, None, None, operation_id)


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


def _assert_journals_balance(factory: sessionmaker[Session], world: World) -> int:
    with factory() as session, session.begin():
        rows = session.execute(
            select(
                Journal.id,
                func.count(Posting.id),
                func.coalesce(
                    func.sum(case((Posting.side == "DEBIT", Posting.amount), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((Posting.side == "CREDIT", Posting.amount), else_=0)), 0
                ),
            )
            .select_from(Journal)
            .outerjoin(Posting, Posting.journal_id == Journal.id)
            .where(Journal.organisation_id == world.organisation_id)
            .group_by(Journal.id)
        ).all()
    for journal_id, postings, debit, credit in rows:
        assert postings >= 2, f"journal {journal_id} has {postings} postings"
        assert debit == credit > 0, f"journal {journal_id}: debit {debit} != credit {credit}"
    return len(rows)


def _refund_rows(factory: sessionmaker[Session], world: World) -> dict[str, tuple[int, str]]:
    """operation public id -> (amount, status) for every refund of the world."""
    with factory() as session, session.begin():
        rows = session.execute(
            select(ProviderOperation.public_id, Refund.amount, Refund.status)
            .join(Refund, Refund.provider_operation_id == ProviderOperation.id)
            .where(Refund.organisation_id == world.organisation_id)
        ).all()
    return {public_id: (amount, status) for public_id, amount, status in rows}


def _read_refundable(
    factory: sessionmaker[Session], world: World, payment_id: str
) -> dict[str, int]:
    view = json.loads(
        read_payment(
            factory,
            organisation_id=world.organisation_id,
            environment_id=world.environment_id,
            payment_public_id=payment_id,
        ).body
    )
    return {
        key: int(view[key])
        for key in ("capturedAmount", "reservedOrRefundedAmount", "refundableAmount")
    }


def _assert_refund_properties(
    factory: sessionmaker[Session],
    world: World,
    provider: FakeProvider,
    payment_id: str,
    captured: int,
    attempts: list[Attempt],
) -> None:
    rows = _refund_rows(factory, world)
    accepted = [a for a in attempts if a.accepted]
    assert {a.operation_id for a in accepted} == set(rows)

    # Σ successful refunds <= captured, and every rejection is the documented one
    assert sum(amount for amount, status in rows.values() if status == "SUCCEEDED") <= captured
    for attempt in attempts:
        if not attempt.accepted:
            assert attempt.error_code == EXCEEDS, attempt
            assert attempt.available is not None and attempt.amount > attempt.available

    # refundableAmount == captured - Σ reserved (PROCESSING / REQUIRES_REVIEW / SUCCEEDED)
    reserved = sum(amount for amount, status in rows.values() if status in RESERVING)
    view = _read_refundable(factory, world, payment_id)
    assert view["capturedAmount"] == captured
    assert view["reservedOrRefundedAmount"] == reserved
    assert view["refundableAmount"] == captured - reserved >= 0

    # Journals: the capture plus one per succeeded refund, all balanced
    succeeded = sum(1 for _, status in rows.values() if status == "SUCCEEDED")
    assert _assert_journals_balance(factory, world) == 1 + succeeded
    provider.assert_exactly_once()
    assert provider.mutation_count() == 2 + len(rows)  # authorize + capture + each refund


captured_amounts = st.integers(min_value=100, max_value=200_000)
refund_requests = st.lists(st.integers(min_value=1, max_value=120_000), min_size=1, max_size=12)


@given(
    captured=captured_amounts,
    requests=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=120_000),
            st.sampled_from([Outcome.SUCCEED] * 3 + [Outcome.DECLINE, Outcome.LOSE_RESPONSE]),
        ),
        min_size=1,
        max_size=12,
    ),
)
def test_sequential_partial_refunds_never_exceed_captured(
    factory: sessionmaker[Session], captured: int, requests: list[tuple[int, Outcome]]
) -> None:
    world = build_world(factory, label="prop-seq")
    provider = FakeProvider()
    payment_id = paid_payment(factory, world, provider, amount=captured).payment_id

    attempts: list[Attempt] = []
    for amount, outcome in requests:
        rows = _refund_rows(factory, world)
        remaining = captured - sum(a for a, status in rows.values() if status in RESERVING)
        attempt = _try_refund(factory, world, payment_id, amount, provider, outcome=outcome)
        attempts.append(attempt)
        # Exact prediction: accepted iff it fits what is still unreserved right now.
        assert attempt.accepted == (amount <= remaining), (amount, remaining, attempt)
        if not attempt.accepted:
            assert attempt.error_code == EXCEEDS and attempt.available == remaining
        assert _read_refundable(factory, world, payment_id)["refundableAmount"] == (
            captured
            - sum(a for a, status in _refund_rows(factory, world).values() if status in RESERVING)
        )

    # Lost responses are recovered by lookup; afterwards nothing is in flight.
    for operation_id, (_, status) in _refund_rows(factory, world).items():
        if status == "PROCESSING":
            recover(factory, world, operation_id, provider)
    assert all(status != "PROCESSING" for _, status in _refund_rows(factory, world).values())
    _assert_refund_properties(factory, world, provider, payment_id, captured, attempts)


@given(captured=captured_amounts, requests=refund_requests)
def test_concurrent_partial_refunds_never_exceed_captured(
    factory: sessionmaker[Session], captured: int, requests: list[int]
) -> None:
    world = build_world(factory, label="prop-conc")
    provider = FakeProvider()
    payment_id = paid_payment(factory, world, provider, amount=captured).payment_id

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [
            pool.submit(_try_refund, factory, world, payment_id, amount, provider)
            for amount in requests
        ]
        attempts = [future.result() for future in futures]

    assert sum(a.amount for a in attempts if a.accepted) <= captured
    # With a cooperative provider the reservation only grows, so a rejected request must
    # also exceed what finally remains.
    final_remaining = captured - sum(a.amount for a in attempts if a.accepted)
    for attempt in attempts:
        if not attempt.accepted:
            assert attempt.error_code == EXCEEDS
            assert attempt.amount > final_remaining
    assert all(status == "SUCCEEDED" for _, status in _refund_rows(factory, world).values())
    _assert_refund_properties(factory, world, provider, payment_id, captured, attempts)
