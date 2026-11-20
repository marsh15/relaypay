"""Invariant 16 (STORED_BALANCE_DRIFT): the stored running totals equal the ledger (ADR-014)."""

from dataclasses import replace

from relaypay.proof.invariants import check_invariants
from relaypay.proof.snapshot_types import InvariantCode, LedgerAccountBalanceSnap, SystemSnapshot

from tests.support.snapshots import AVAILABLE, CLEARING, PENDING, golden_snapshot

DRIFT = InvariantCode.STORED_BALANCE_DRIFT.value


def _balances(snapshot: SystemSnapshot) -> tuple[LedgerAccountBalanceSnap, ...]:
    assert snapshot.ledger_account_balances is not None
    return snapshot.ledger_account_balances


def _drift(snapshot: SystemSnapshot) -> list[tuple[str, str]]:
    return [
        (violation.subject, violation.message)
        for violation in check_invariants(snapshot, quiesced=True)
        if violation.code == DRIFT
    ]


def test_golden_stored_balances_match_the_postings() -> None:
    golden = golden_snapshot()
    pending = next(row for row in _balances(golden) if row.account_id == PENDING)
    # pi_1 captured 100_000 and refunded 30_000; pi_2 captured 50_000 and settled it.
    assert (pending.credit_total, pending.debit_total) == (150_000, 80_000)
    assert _drift(golden) == []


def test_snapshot_without_stored_balances_skips_the_check() -> None:
    assert _drift(replace(golden_snapshot(), ledger_account_balances=None)) == []


def test_posting_count_drift_is_reported() -> None:
    golden = golden_snapshot()
    rows = tuple(
        replace(row, posting_count=row.posting_count + 1) if row.account_id == AVAILABLE else row
        for row in _balances(golden)
    )
    [(subject, message)] = _drift(replace(golden, ledger_account_balances=rows))
    assert subject == AVAILABLE
    assert "stored credit, debit, count" in message


def test_missing_row_for_a_merchant_account_is_reported() -> None:
    golden = golden_snapshot()
    rows = tuple(row for row in _balances(golden) if row.account_id != PENDING)
    assert _drift(replace(golden, ledger_account_balances=rows)) == [
        (PENDING, "merchant account has 0 stored balance rows, expected 1")
    ]


def test_duplicate_row_is_reported() -> None:
    golden = golden_snapshot()
    pending = next(row for row in _balances(golden) if row.account_id == PENDING)
    rows = (*_balances(golden), pending)
    assert _drift(replace(golden, ledger_account_balances=rows)) == [
        (PENDING, "merchant account has 2 stored balance rows, expected 1")
    ]


def test_platform_account_with_a_row_is_reported() -> None:
    golden = golden_snapshot()
    rows = (*_balances(golden), LedgerAccountBalanceSnap(CLEARING, 0, 0, 0))
    assert _drift(replace(golden, ledger_account_balances=rows)) == [
        (CLEARING, "platform account has a stored balance row")
    ]


def test_row_for_an_unknown_account_is_reported() -> None:
    golden = golden_snapshot()
    rows = (*_balances(golden), LedgerAccountBalanceSnap("acct-ghost", 0, 0, 0))
    assert _drift(replace(golden, ledger_account_balances=rows)) == [
        ("acct-ghost", "stored balance row names no ledger account")
    ]
