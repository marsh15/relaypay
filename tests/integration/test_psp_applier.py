"""M17: inbox PSP events become WEBHOOK evidence and finalize through the one finalizer.

``decide`` is hand-written by the maintainer (PROVENANCE.md), so these tests inject fixed
deciders: they test the applier's plumbing, not the ordering rule.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.provider_operations.models import (
    OperationHistory,
    ProviderAttempt,
    ProviderOperation,
)
from relaypay.psp import applier
from relaypay.psp.applier import (
    MAX_EVENT_ATTEMPTS,
    claim_psp_events,
    process_psp_event,
    run_psp_event_batch,
)
from relaypay.psp.inbox import accept_psp_event
from relaypay.psp.models import PspEvent
from relaypay.psp.types import Apply, Decision, Ignore, NormalizedPspEvent, OperationView
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import ACCOUNT_ID, FakeProvider, Outcome
from tests.support.psp import WEBHOOK_SECRET, signed_event
from tests.support.world import World, authorize, build_world, create_payment, dispatch

pytestmark = [pytest.mark.integration]


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("psp-applier-tests", pool_size=10) as session_factory:
        yield session_factory


def _apply(view: OperationView, event: NormalizedPspEvent) -> Decision:
    del view
    if event.outcome == "PENDING":
        return Ignore("STALE")
    return Apply(event.outcome)


def _ignore(view: OperationView, event: NormalizedPspEvent) -> Decision:
    del view, event
    return Ignore("DUPLICATE")


def _lost_authorization(
    factory: sessionmaker[Session], label: str
) -> tuple[World, ProviderOperation, FakeProvider]:
    """An authorization the provider committed whose response was lost: PROCESSING here."""
    world = build_world(factory, label=label)
    payment = create_payment(factory, world)
    operation_id = authorize(factory, world, payment)
    provider = FakeProvider()
    operation = _operation(factory, operation_id)
    provider.script(operation.stable_provider_key, Outcome.LOSE_RESPONSE)
    dispatch(factory, world, operation_id, provider)
    operation = _operation(factory, operation_id)
    assert operation.status == "PROCESSING"
    return world, operation, provider


def _operation(factory: sessionmaker[Session], public_id: str) -> ProviderOperation:
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == public_id)
        )
        assert operation is not None
        session.expunge(operation)
        return operation


def _effect(provider: FakeProvider, operation: ProviderOperation, **changes: object) -> dict:
    effect = provider.effects[operation.stable_provider_key]
    return {
        "accountId": ACCOUNT_ID,
        "amount": effect.amount,
        "currency": "INR",
        "declineCode": effect.decline_code,
        "effectId": effect.effect_id,
        "operationKind": effect.operation_kind,
        "outcome": effect.outcome,
        "reference": effect.reference,
        "stableKey": effect.stable_key,
    } | changes


def _deliver(factory: sessionmaker[Session], effect: dict) -> PspEvent:
    headers, body = signed_event(effect)
    event, _ = accept_psp_event(
        factory,
        provider="SIMULATOR",
        headers=headers,
        body=body,
        webhook_secret=WEBHOOK_SECRET,
        replay_seconds=300,
    )
    return event


def _run(factory: sessionmaker[Session], world: World, decider: applier.Decider) -> int:
    return run_psp_event_batch(
        factory,
        provider_account_id=ACCOUNT_ID,
        organisation_id=world.organisation_id,
        decider=decider,
    )


def _event(factory: sessionmaker[Session], event: PspEvent) -> PspEvent:
    with factory() as session, session.begin():
        row = session.get(PspEvent, event.id)
        assert row is not None
        return row


def _webhook_attempts(factory: sessionmaker[Session], operation: ProviderOperation) -> int:
    with factory() as session, session.begin():
        return int(
            session.scalar(
                select(func.count()).where(
                    ProviderAttempt.provider_operation_id == operation.id,
                    ProviderAttempt.attempt_kind == "WEBHOOK",
                )
            )
            or 0
        )


def test_without_the_ordering_rule_events_are_deferred(factory: sessionmaker[Session]) -> None:
    world, operation, provider = _lost_authorization(factory, "psp-apply-deferred")
    event = _deliver(factory, _effect(provider, operation))

    assert _run(factory, world, applier.decide) == 1

    row = _event(factory, event)
    assert (row.status, row.outcome_code) == ("DEFERRED", "ORDERING_DECISION_UNAVAILABLE")
    assert _operation(factory, operation.public_id).status == "PROCESSING"
    assert _webhook_attempts(factory, operation) == 0


def test_applied_webhook_finalizes_through_the_finalizer(factory: sessionmaker[Session]) -> None:
    world, operation, provider = _lost_authorization(factory, "psp-apply-success")
    event = _deliver(factory, _effect(provider, operation))

    assert _run(factory, world, _apply) == 1

    assert _event(factory, event).status == "APPLIED"
    assert _operation(factory, operation.public_id).status == "SUCCEEDED"
    assert _webhook_attempts(factory, operation) == 1
    with factory() as session, session.begin():
        actors = session.scalars(
            select(OperationHistory.actor_type).where(
                OperationHistory.provider_operation_id == operation.id,
                OperationHistory.to_status == "SUCCEEDED",
            )
        ).all()
    assert actors == ["WEBHOOK"]

    redelivery = _deliver(factory, _effect(provider, operation))
    assert _run(factory, world, _ignore) == 1
    assert (_event(factory, redelivery).status, _event(factory, redelivery).outcome_code) == (
        "IGNORED",
        "DUPLICATE",
    )
    assert _webhook_attempts(factory, operation) == 1


def test_a_crash_after_recording_resumes_without_a_second_attempt(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, operation, provider = _lost_authorization(factory, "psp-apply-crash")
    event = _deliver(factory, _effect(provider, operation))

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated crash before finalizing")

    monkeypatch.setattr(applier, "finalize_verified_attempt", crash)
    [claim] = claim_psp_events(factory, organisation_id=world.organisation_id)
    with pytest.raises(RuntimeError, match="simulated crash"):
        process_psp_event(factory, claim, provider_account_id=ACCOUNT_ID, decider=_apply)
    assert _webhook_attempts(factory, operation) == 1
    assert _operation(factory, operation.public_id).status == "PROCESSING"

    monkeypatch.undo()
    with factory() as session, session.begin():
        session.execute(
            update(PspEvent)
            .where(PspEvent.id == event.id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert _run(factory, world, _ignore) == 1  # the recorded attempt wins; no new decision

    assert _event(factory, event).status == "APPLIED"
    assert _operation(factory, operation.public_id).status == "SUCCEEDED"
    assert _webhook_attempts(factory, operation) == 1


def test_mismatched_evidence_sends_the_operation_to_review(
    factory: sessionmaker[Session],
) -> None:
    world, operation, provider = _lost_authorization(factory, "psp-apply-mismatch")
    effect = _effect(provider, operation)
    event = _deliver(factory, effect | {"amount": effect["amount"] + 1})

    assert _run(factory, world, _apply) == 1

    row = _event(factory, event)
    assert (row.status, row.outcome_code) == ("REVIEW", "MISMATCHED_EVIDENCE")
    reviewed = _operation(factory, operation.public_id)
    assert (reviewed.status, reviewed.review_reason) == ("REQUIRES_REVIEW", "MISMATCHED_EVIDENCE")
    assert _webhook_attempts(factory, operation) == 0


def test_a_repeatedly_failing_event_is_dead_lettered(factory: sessionmaker[Session]) -> None:
    world, operation, provider = _lost_authorization(factory, "psp-apply-dead-letter")
    event = _deliver(factory, _effect(provider, operation))
    with factory() as session, session.begin():
        session.execute(
            update(PspEvent).where(PspEvent.id == event.id).values(attempt_count=MAX_EVENT_ATTEMPTS)
        )

    assert claim_psp_events(factory, organisation_id=world.organisation_id) == []

    row = _event(factory, event)
    assert (row.status, row.outcome_code) == ("DEAD_LETTER", "ATTEMPTS_EXHAUSTED")
    assert _operation(factory, operation.public_id).status == "PROCESSING"


def test_one_failing_event_does_not_stall_the_batch(factory: sessionmaker[Session]) -> None:
    world, first, first_provider = _lost_authorization(factory, "psp-apply-poison")
    poison = _deliver(factory, _effect(first_provider, first))
    second_payment = create_payment(factory, world)
    second_id = authorize(factory, world, second_payment)
    second_provider = FakeProvider()
    second = _operation(factory, second_id)
    second_provider.script(second.stable_provider_key, Outcome.LOSE_RESPONSE)
    dispatch(factory, world, second_id, second_provider)
    healthy = _deliver(factory, _effect(second_provider, second))

    def poisoned(view: OperationView, event: NormalizedPspEvent) -> Decision:
        if event.event_id == poison.provider_event_id:
            raise ValueError("poison")
        return _apply(view, event)

    assert _run(factory, world, poisoned) == 2

    assert _event(factory, poison).status == "PROCESSING"  # retried after the lease lapses
    assert _event(factory, poison).attempt_count == 1
    assert _event(factory, healthy).status == "APPLIED"
    assert _operation(factory, second_id).status == "SUCCEEDED"
