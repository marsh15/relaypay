"""Red tests for the hand-written invariant checker, written from its specification only.

The specification is the docstring of ``relaypay.proof.invariants.check_invariants`` and the plan
in docs/plans/v2.0-never-loses-a-paisa.md ("User-owned modules", item 1). Nothing here reads or
mirrors an implementation: the golden snapshot and the single-defect mutations are plain data
(``tests/support/snapshots.py``). Until the maintainer implements the checker these fail with
``NotImplementedError``; run them with ``make handwritten``.
"""

import pytest
from relaypay.proof.invariants import check_invariants
from relaypay.proof.snapshot_types import InvariantCode, SystemSnapshot, Violation

from tests.support.snapshots import (
    MUTATIONS,
    QUIESCED_ONLY,
    golden_snapshot,
    mutated_snapshot,
    with_in_flight_operation,
    with_operation_in_review,
)

pytestmark = pytest.mark.handwritten

ALL_CODES = list(InvariantCode)


def _codes(violations: list[Violation]) -> set[str]:
    return {violation.code for violation in violations}


@pytest.mark.parametrize("quiesced", [True, False])
def test_golden_snapshot_has_no_violations(quiesced: bool) -> None:
    assert set(MUTATIONS) == set(InvariantCode)
    assert len(ALL_CODES) == 15
    assert check_invariants(golden_snapshot(), quiesced=quiesced) == []


@pytest.mark.parametrize("quiesced", [True, False])
@pytest.mark.parametrize("code", ALL_CODES, ids=lambda code: code.value)
def test_single_mutation_yields_exactly_its_own_code(code: InvariantCode, quiesced: bool) -> None:
    violations = check_invariants(mutated_snapshot(code), quiesced=quiesced)
    # Invariants 13 and 15 are only evaluated once the system is quiesced.
    expected = set() if code in QUIESCED_ONLY and not quiesced else {code.value}
    assert _codes(violations) == expected


@pytest.mark.parametrize("code", ALL_CODES, ids=lambda code: code.value)
def test_violations_are_well_formed(code: InvariantCode) -> None:
    violations = check_invariants(mutated_snapshot(code), quiesced=True)
    assert violations, "the mutation must be detected"
    for violation in violations:
        assert isinstance(violation, Violation)
        assert violation.code in {member.value for member in InvariantCode}
        assert violation.subject
        assert violation.message


def test_in_flight_operations_are_legal_while_not_quiesced() -> None:
    snapshot = with_in_flight_operation(golden_snapshot())
    assert check_invariants(snapshot, quiesced=False) == []


def test_in_flight_operations_are_reported_once_quiesced() -> None:
    snapshot = with_in_flight_operation(golden_snapshot())
    assert _codes(check_invariants(snapshot, quiesced=True)) == {InvariantCode.NOT_QUIESCED.value}


def test_operation_resting_in_review_is_accepted_when_quiesced() -> None:
    snapshot = with_operation_in_review(golden_snapshot())
    assert check_invariants(snapshot, quiesced=True) == []
    assert check_invariants(snapshot, quiesced=False) == []


def test_effect_without_operation_is_tolerated_while_not_quiesced() -> None:
    snapshot = mutated_snapshot(InvariantCode.UNKNOWN_PROVIDER_EFFECT)
    assert check_invariants(snapshot, quiesced=False) == []


def test_unknown_effect_beside_in_flight_operation_is_still_reported_when_quiesced() -> None:
    snapshot = with_in_flight_operation(mutated_snapshot(InvariantCode.UNKNOWN_PROVIDER_EFFECT))
    assert _codes(check_invariants(snapshot, quiesced=True)) == {
        InvariantCode.UNKNOWN_PROVIDER_EFFECT.value,
        InvariantCode.NOT_QUIESCED.value,
    }


def test_independent_defects_are_all_reported() -> None:
    snapshot = golden_snapshot()
    wanted = {
        InvariantCode.NEGATIVE_BALANCE,
        InvariantCode.STATUS_DRIFT,
        InvariantCode.ACKED_OP_MISSING,
    }
    for code in wanted:
        snapshot = MUTATIONS[code](snapshot)
    assert _codes(check_invariants(snapshot, quiesced=True)) == {code.value for code in wanted}


def test_output_is_deterministic() -> None:
    snapshot: SystemSnapshot = golden_snapshot()
    for code in (
        InvariantCode.NEGATIVE_BALANCE,
        InvariantCode.STATUS_DRIFT,
        InvariantCode.ACKED_OP_MISSING,
        InvariantCode.JOURNAL_UNBALANCED,
    ):
        snapshot = MUTATIONS[code](snapshot)
    first = check_invariants(snapshot, quiesced=True)
    assert first, "the combined mutations must be detected"
    assert check_invariants(snapshot, quiesced=True) == first
    rebuilt = SystemSnapshot(**{name: getattr(snapshot, name) for name in snapshot.__slots__})
    assert check_invariants(rebuilt, quiesced=True) == first


def test_empty_snapshot_is_sound() -> None:
    assert check_invariants(SystemSnapshot(), quiesced=True) == []
    assert check_invariants(SystemSnapshot(), quiesced=False) == []
