"""The payment status table: full 9x9 matrix, and migration 0019 must not drift from it."""

import importlib.util
import re
from itertools import product
from pathlib import Path
from types import ModuleType

import pytest
from relaypay.errors import RelayPayError
from relaypay.payments import transitions
from relaypay.payments.models import PaymentIntent
from relaypay.payments.transitions import ALLOWED, PaymentStatus, is_allowed, transition

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "migrations" / "relaypay" / "versions" / "0019_payment_state_machine.py"

S = PaymentStatus

# Written out independently of ALLOWED so a slip in the table cannot hide in the test.
LEGAL: dict[PaymentStatus, set[PaymentStatus]] = {
    S.CREATED: {S.REQUIRES_ACTION, S.AUTHORISED, S.FAILED, S.CANCELLED},
    S.REQUIRES_ACTION: {S.AUTHORISED, S.FAILED, S.CANCELLED, S.EXPIRED},
    S.AUTHORISED: {S.CAPTURED, S.CANCELLED, S.EXPIRED, S.FAILED},
    S.CAPTURED: {S.PARTIALLY_REFUNDED, S.REFUNDED},
    S.PARTIALLY_REFUNDED: {S.PARTIALLY_REFUNDED, S.REFUNDED},
    S.REFUNDED: set(),
    S.FAILED: set(),
    S.EXPIRED: set(),
    S.CANCELLED: set(),
}


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0019", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enum_has_the_nine_states() -> None:
    assert [status.value for status in PaymentStatus] == [
        "created",
        "requires_action",
        "authorised",
        "captured",
        "partially_refunded",
        "refunded",
        "failed",
        "expired",
        "cancelled",
    ]


def test_allowed_is_exactly_the_documented_sixteen_pairs() -> None:
    expected = {(a.value, b.value) for a, targets in LEGAL.items() for b in targets}
    assert len(expected) == 16
    assert expected == ALLOWED


@pytest.mark.parametrize(("source", "target"), list(product(PaymentStatus, PaymentStatus)))
def test_full_matrix(source: PaymentStatus, target: PaymentStatus) -> None:
    legal = target in LEGAL[source]
    assert is_allowed(source.value, target.value) is legal
    assert ((source.value, target.value) in ALLOWED) is legal


def test_only_partially_refunded_may_repeat() -> None:
    assert {s for s in PaymentStatus if (s.value, s.value) in ALLOWED} == {S.PARTIALLY_REFUNDED}


def test_terminal_states_have_no_exits() -> None:
    for terminal in (S.REFUNDED, S.FAILED, S.EXPIRED, S.CANCELLED):
        assert not [pair for pair in ALLOWED if pair[0] == terminal.value]


class _FakeSession:
    """Never reached: the stale and illegal checks run before any write."""


def test_transition_rejects_stale_view_and_illegal_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    payment = PaymentIntent()
    payment.status = S.CREATED.value
    payment.status_version = 3

    monkeypatch.setattr(transitions, "_lock_versions", lambda session, payment_id: ("created", 4))
    with pytest.raises(RelayPayError) as stale:
        transition(_FakeSession(), payment, S.AUTHORISED, reason="X", actor="TEST")  # type: ignore[arg-type]
    assert stale.value.code == "PAYMENT_STATE_STALE"

    monkeypatch.setattr(transitions, "_lock_versions", lambda session, payment_id: ("created", 3))
    with pytest.raises(RelayPayError) as illegal:
        transition(_FakeSession(), payment, S.CAPTURED, reason="X", actor="TEST")  # type: ignore[arg-type]
    assert illegal.value.code == "ILLEGAL_PAYMENT_TRANSITION"
    assert illegal.value.details == {"from": "created", "to": "captured"}
    assert payment.status == "created" and payment.status_version == 3


def test_migration_statuses_match_enum() -> None:
    assert tuple(_migration().STATUSES) == tuple(status.value for status in PaymentStatus)


def test_migration_transition_table_matches_allowed() -> None:
    migration = _migration()
    assert len(migration.ALLOWED_TRANSITIONS) == len(set(migration.ALLOWED_TRANSITIONS))
    assert frozenset(migration.ALLOWED_TRANSITIONS) == ALLOWED


def test_trigger_function_sql_inlines_exactly_allowed() -> None:
    """Parse the SQL the migration actually executes, not just its Python constant."""
    migration = _migration()
    sql = migration.transition_function_sql()
    block = sql[sql.index("VALUES") : sql.index("AS allowed")]
    pairs = re.findall(r"\('([a-z_]+)', '([a-z_]+)'\)", block)
    assert len(pairs) == len(set(pairs))
    assert frozenset(pairs) == ALLOWED
    assert f"ERRCODE = '{migration.SQLSTATE}'" in sql
