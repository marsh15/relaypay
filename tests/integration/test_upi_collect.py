"""M17 T17.0/T17.3: UPI collect: a pending authorization that waits for the customer.

The provider says "pending, and the customer has something to do" with a signed ``nextAction``.
RelayPay then records the next action, moves the payment to ``requires_action``, and polls without
spending its five-lookup review budget until ``pending_deadline_at``; after the deadline a person
decides. Ordinary ambiguity (a provider that merely never settles) keeps the old budget.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from relaypay.mock_provider.models import ProviderAccount, ProviderEffect
from relaypay.mock_provider.service import (
    EffectCommand,
    apply_effect,
    configure_fault,
    lookup_effect,
)
from relaypay.payments.models import Authorization, PaymentIntent
from relaypay.payments.service import read_payment_v2
from relaypay.provider_operations.models import ProviderOperation
from relaypay.provider_operations.validation import ProviderResult
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import provider_factory, relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.world import (
    World,
    authorize,
    build_world,
    create_payment,
    dispatch,
    operation_status,
    recover,
)

pytestmark = pytest.mark.integration

REVIEW_BUDGET = 5  # lookups before an ambiguous operation goes to review


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("upi-collect-tests", pool_size=6) as session_factory:
        yield session_factory


def _pending_authorization(
    factory: sessionmaker[Session], label: str
) -> tuple[World, str, str, FakeProvider]:
    """An authorization the provider holds as a UPI collect, pending on the customer."""
    world = build_world(factory, label=label)
    provider = FakeProvider(upi_collect_pending=True)
    payment = create_payment(factory, world)
    operation_id = authorize(factory, world, payment)
    with factory() as session, session.begin():
        key = session.scalar(
            select(ProviderOperation.stable_provider_key).where(
                ProviderOperation.public_id == operation_id
            )
        )
    assert key is not None
    provider.script(str(key), Outcome.PENDING)
    dispatch(factory, world, operation_id, provider)
    return world, payment, operation_id, provider


def _payment_status(factory: sessionmaker[Session], payment: str) -> str:
    with factory() as session, session.begin():
        return str(
            session.scalar(select(PaymentIntent.status).where(PaymentIntent.public_id == payment))
        )


def _authorization(factory: sessionmaker[Session], payment: str) -> Authorization:
    with factory() as session, session.begin():
        row = session.scalar(
            select(Authorization)
            .join(PaymentIntent, PaymentIntent.id == Authorization.payment_intent_id)
            .where(PaymentIntent.public_id == payment)
        )
        assert row is not None
        session.expunge(row)
        return row


def _operation(factory: sessionmaker[Session], operation_id: str) -> ProviderOperation:
    with factory() as session, session.begin():
        row = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert row is not None
        session.expunge(row)
        return row


def _stable_key(factory: sessionmaker[Session], operation_id: str) -> str:
    return _operation(factory, operation_id).stable_provider_key


def test_a_pending_upi_collect_moves_the_payment_to_requires_action(
    factory: sessionmaker[Session],
) -> None:
    world, payment, operation_id, _ = _pending_authorization(factory, "upi-requires-action")

    assert operation_status(factory, world, operation_id) == "PROCESSING"
    assert _payment_status(factory, payment) == "requires_action"
    authorization = _authorization(factory, payment)
    assert authorization.next_action is not None
    assert authorization.next_action["type"] == "UPI_COLLECT"
    operation = _operation(factory, operation_id)
    assert operation.pending_deadline_at is not None
    wait = operation.pending_deadline_at - datetime.now(UTC)
    assert timedelta(minutes=14) < wait <= timedelta(minutes=15)

    view = json.loads(
        read_payment_v2(
            factory,
            organisation_id=world.organisation_id,
            environment_id=world.environment_id,
            payment_public_id=payment,
        ).body
    )
    assert view["status"] == "requires_action"
    assert view["authorization"]["nextAction"]["type"] == "UPI_COLLECT"


def test_polling_a_customer_wait_does_not_spend_the_review_budget(
    factory: sessionmaker[Session],
) -> None:
    world, payment, operation_id, provider = _pending_authorization(factory, "upi-budget")

    for _ in range(REVIEW_BUDGET + 4):
        recover(factory, world, operation_id, provider)

    assert operation_status(factory, world, operation_id) == "PROCESSING"
    assert _payment_status(factory, payment) == "requires_action"
    assert provider.lookup_count() >= REVIEW_BUDGET + 4


def test_an_ambiguous_pending_without_a_next_action_still_hits_the_review_budget(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="upi-control")
    provider = FakeProvider()  # PENDING without a nextAction: the provider just never settles
    payment = create_payment(factory, world)
    operation_id = authorize(factory, world, payment)
    provider.script(_stable_key(factory, operation_id), Outcome.PENDING)
    dispatch(factory, world, operation_id, provider)

    for _ in range(REVIEW_BUDGET + 1):
        if operation_status(factory, world, operation_id) != "PROCESSING":
            break
        recover(factory, world, operation_id, provider)

    assert operation_status(factory, world, operation_id) == "REQUIRES_REVIEW"
    assert _operation(factory, operation_id).review_reason == "PROVIDER_INDETERMINATE"
    assert _payment_status(factory, payment) == "created"


def test_the_customer_approving_authorizes_and_clears_the_next_action(
    factory: sessionmaker[Session],
) -> None:
    world, payment, operation_id, provider = _pending_authorization(factory, "upi-approved")
    provider.resolve(_stable_key(factory, operation_id), Outcome.SUCCEED)

    recover(factory, world, operation_id, provider)

    assert operation_status(factory, world, operation_id) == "SUCCEEDED"
    assert _payment_status(factory, payment) == "authorised"
    authorization = _authorization(factory, payment)
    assert authorization.status == "SUCCEEDED" and authorization.next_action is None
    provider.assert_exactly_once()


def test_the_customer_declining_fails_the_payment_and_clears_the_next_action(
    factory: sessionmaker[Session],
) -> None:
    world, payment, operation_id, provider = _pending_authorization(factory, "upi-declined")
    provider.resolve(_stable_key(factory, operation_id), Outcome.DECLINE)

    recover(factory, world, operation_id, provider)

    assert operation_status(factory, world, operation_id) == "FAILED"
    assert _payment_status(factory, payment) == "failed"
    assert _authorization(factory, payment).next_action is None


def test_a_customer_who_never_answers_goes_to_review_at_the_deadline(
    factory: sessionmaker[Session],
) -> None:
    world, payment, operation_id, provider = _pending_authorization(factory, "upi-deadline")
    with factory() as session, session.begin():
        session.execute(
            update(ProviderOperation)
            .where(ProviderOperation.public_id == operation_id)
            .values(pending_deadline_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    recover(factory, world, operation_id, provider)

    assert operation_status(factory, world, operation_id) == "REQUIRES_REVIEW"
    assert _operation(factory, operation_id).review_reason == "PENDING_DEADLINE_EXCEEDED"
    # The payment is still waiting on the customer; a person decides what that means.
    assert _payment_status(factory, payment) == "requires_action"


@pytest.mark.parametrize(
    "document",
    [
        {"outcome": "SUCCEEDED", "operationKind": "AUTHORIZE"},
        {"outcome": "PENDING", "operationKind": "CAPTURE"},
    ],
)
def test_only_a_pending_authorization_may_carry_a_next_action(document: dict[str, str]) -> None:
    base = {
        "accountId": "acct",
        "stableKey": "authorize:pay_1",
        "operationKind": "AUTHORIZE",
        "reference": "ref",
        "amount": 100,
        "currency": "INR",
        "outcome": "PENDING",
        "declineCode": None,
        "effectId": "eff",
        "nextAction": {"type": "UPI_COLLECT", "vpa": "a@b", "expiresAt": "2099-01-01T00:00:00Z"},
    }
    assert ProviderResult.model_validate_json(json.dumps(base)).nextAction is not None
    with pytest.raises(ValidationError):
        ProviderResult.model_validate_json(json.dumps({**base, **document}))


# ----- the provider simulator's side ---------------------------------------------------------


@pytest.fixture
def provider_factory_session() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with provider_factory(pool_size=4) as factory:
        yield factory


def test_the_simulator_holds_a_upi_collect_then_settles_it_when_the_customer_approves(
    provider_factory_session: sessionmaker[Session],
) -> None:
    factory = provider_factory_session
    secret = "upi-simulator-secret"
    account = f"acct_upi_{hashlib.sha256(str(datetime.now(UTC)).encode()).hexdigest()[:12]}"
    with factory() as session, session.begin():
        session.add(
            ProviderAccount(
                public_id=account,
                name="UPI test",
                signing_secret_digest=hashlib.sha256(secret.encode()).digest(),
            )
        )
    command = EffectCommand(account, "authorize:pay_upi", "AUTHORIZE", "ref_upi", 5_000, "INR")
    configure_fault(
        factory, account_public_id=account, stable_key="authorize:pay_upi", fault_type="UPI_COLLECT"
    )

    first = json.loads(apply_effect(factory, command=command, signing_secret=secret).body)
    still_waiting = json.loads(
        lookup_effect(
            factory,
            account_public_id=account,
            stable_key="authorize:pay_upi",
            signing_secret=secret,
        ).body
    )
    with factory() as session, session.begin():
        session.execute(
            update(ProviderEffect)
            .where(ProviderEffect.stable_key == "authorize:pay_upi")
            .values(resolve_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    settled = lookup_effect(
        factory, account_public_id=account, stable_key="authorize:pay_upi", signing_secret=secret
    )
    after = json.loads(settled.body)

    assert first["outcome"] == "PENDING" and first["nextAction"]["type"] == "UPI_COLLECT"
    assert still_waiting["outcome"] == "PENDING" and "nextAction" in still_waiting
    assert after["outcome"] == "SUCCEEDED" and "nextAction" not in after
    assert settled.headers["X-Provider-Signature"]  # the settled answer is signed too
    # A settled effect stays settled and byte-identical on later lookups.
    again = lookup_effect(
        factory, account_public_id=account, stable_key="authorize:pay_upi", signing_secret=secret
    )
    assert again.body == settled.body


def test_the_upi_collect_fault_applies_only_to_authorizations(
    provider_factory_session: sessionmaker[Session],
) -> None:
    factory = provider_factory_session
    secret = "upi-simulator-secret-2"
    account = f"acct_upi2_{hashlib.sha256(str(datetime.now(UTC)).encode()).hexdigest()[:12]}"
    with factory() as session, session.begin():
        session.add(
            ProviderAccount(
                public_id=account,
                name="UPI test 2",
                signing_secret_digest=hashlib.sha256(secret.encode()).digest(),
            )
        )
    configure_fault(
        factory, account_public_id=account, stable_key="capture:pay_x", fault_type="UPI_COLLECT"
    )
    capture = EffectCommand(account, "capture:pay_x", "CAPTURE", "ref_x", 5_000, "INR")

    body = json.loads(apply_effect(factory, command=capture, signing_secret=secret).body)

    assert body["outcome"] == "SUCCEEDED" and "nextAction" not in body
