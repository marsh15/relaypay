"""Red specification tests for the hand-written PSP ordering decision (see PROVENANCE.md).

Written from the docstring of ``relaypay.psp.ordering.decide`` only; they fail with
NotImplementedError until the maintainer implements it. Run with ``make handwritten``.
"""

from typing import get_args

import pytest
from hypothesis import given
from hypothesis import strategies as st
from relaypay.psp.ordering import decide
from relaypay.psp.types import (
    Apply,
    Decision,
    EventOutcome,
    Ignore,
    NormalizedPspEvent,
    OperationStatus,
    OperationView,
    Review,
)

pytestmark = pytest.mark.handwritten

TERMINAL = {"SUCCEEDED": "SUCCEEDED", "DECLINED": "FAILED"}


def _view(status: OperationStatus, applied: frozenset[str] = frozenset()) -> OperationView:
    return OperationView(status=status, kind="AUTHORIZE", amount=100_000, applied_event_ids=applied)


def _event(outcome: EventOutcome, event_id: str = "evt_1") -> NormalizedPspEvent:
    return NormalizedPspEvent(
        provider="SIMULATOR",
        event_id=event_id,
        outcome=outcome,
        decline_code="DO_NOT_HONOR" if outcome == "DECLINED" else None,
    )


@pytest.mark.parametrize(
    ("status", "outcome", "expected"),
    [
        ("PROCESSING", "SUCCEEDED", Apply("SUCCEEDED")),
        ("PROCESSING", "DECLINED", Apply("DECLINED")),
        ("PROCESSING", "PENDING", Ignore("STALE")),
        ("REQUIRES_REVIEW", "SUCCEEDED", Apply("SUCCEEDED")),
        ("REQUIRES_REVIEW", "DECLINED", Apply("DECLINED")),
        ("REQUIRES_REVIEW", "PENDING", Ignore("STALE")),
        ("SUCCEEDED", "SUCCEEDED", Ignore("DUPLICATE")),
        ("FAILED", "DECLINED", Ignore("DUPLICATE")),
        ("SUCCEEDED", "PENDING", Ignore("STALE")),
        ("FAILED", "PENDING", Ignore("STALE")),
        ("SUCCEEDED", "DECLINED", Review("CONTRADICTS_TERMINAL_STATE")),
        ("FAILED", "SUCCEEDED", Review("CONTRADICTS_TERMINAL_STATE")),
    ],
)
def test_decision_table(status: OperationStatus, outcome: EventOutcome, expected: Decision) -> None:
    assert decide(_view(status), _event(outcome)) == expected


@pytest.mark.parametrize("status", get_args(OperationStatus))
@pytest.mark.parametrize("outcome", get_args(EventOutcome))
def test_an_already_applied_event_is_a_duplicate(
    status: OperationStatus, outcome: EventOutcome
) -> None:
    view = _view(status, frozenset({"evt_1"}))
    assert decide(view, _event(outcome, "evt_1")) == Ignore("DUPLICATE")


def _fold(deliveries: list[NormalizedPspEvent]) -> tuple[OperationStatus, list[Decision]]:
    """Drive ``decide`` the way the applier does, with the finalizer's terminal no-op."""
    status: OperationStatus = "PROCESSING"
    applied: set[str] = set()
    decisions: list[Decision] = []
    for event in deliveries:
        decision = decide(_view(status, frozenset(applied)), event)
        decisions.append(decision)
        if isinstance(decision, Apply):
            applied.add(event.event_id)
            if status in {"PROCESSING", "REQUIRES_REVIEW"}:
                status = "SUCCEEDED" if decision.outcome == "SUCCEEDED" else "FAILED"
    return status, decisions


@st.composite
def _legal_deliveries(
    draw: st.DrawFn,
) -> tuple[list[NormalizedPspEvent], list[NormalizedPspEvent]]:
    """One operation's legal event set (pending reports, then one terminal outcome), in order,
    and the same set permuted with every event delivered one to three times."""
    pending = draw(st.integers(min_value=0, max_value=3))
    terminal: EventOutcome = draw(st.sampled_from(["SUCCEEDED", "DECLINED"]))
    in_order = [_event("PENDING", f"evt_pending_{index}") for index in range(pending)]
    in_order.append(_event(terminal, "evt_terminal"))
    repeated = [
        event for event in in_order for _ in range(draw(st.integers(min_value=1, max_value=3)))
    ]
    return in_order, draw(st.permutations(repeated))


@given(_legal_deliveries())
def test_any_order_and_redelivery_reaches_the_in_order_result(
    deliveries: tuple[list[NormalizedPspEvent], list[NormalizedPspEvent]],
) -> None:
    in_order, shuffled = deliveries
    expected, _ = _fold(in_order)
    final, decisions = _fold(shuffled)

    assert final == expected == TERMINAL[in_order[-1].outcome]
    assert not any(isinstance(decision, Review) for decision in decisions)
    assert sum(isinstance(decision, Apply) for decision in decisions) == 1
