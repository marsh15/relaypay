"""Golden-valid ``SystemSnapshot`` plus one single-defect mutation per invariant code.

``golden_snapshot()`` is a small hand-built world that satisfies every invariant by
construction (see the specification on ``relaypay.proof.invariants.check_invariants``). Each
builder in ``MUTATIONS`` returns a copy of it with one defect that should surface as exactly one
``InvariantCode``. This module builds data only; it computes no violations.

World (one organisation, one environment, one merchant, INR paise):

* payment ``pi_1`` (100_000): authorized, captured, partially refunded by 30_000, unsettled.
* payment ``pi_2`` (50_000): authorized, captured, settled; a 20_000 payout is reserved.
* merchant balances: pending 70_000, available 50_000, receivable 0, reserved 20_000.
* every operation SUCCEEDED, with terminal idempotency bytes equal to the operation's, one
  provider effect per stable key, and every payment ``status`` None (the M16 column).

Only invariants 13 and 15 depend on ``quiesced``; see ``QUIESCED_ONLY``.
"""

import hashlib
from collections.abc import Callable
from dataclasses import fields, replace
from typing import TYPE_CHECKING, Any

from relaypay.proof.snapshot_types import (
    AckedOp,
    AuthorizationSnap,
    BalanceTransactionSnap,
    CaptureSnap,
    IdempotencyRecordSnap,
    InvariantCode,
    JournalSnap,
    LedgerAccountSnap,
    MerchantBalanceSnap,
    OperationHistorySnap,
    PaymentIntentSnap,
    PayoutReservationSnap,
    PayoutSnap,
    PostingSnap,
    ProviderEffectSnap,
    ProviderOperationSnap,
    RefundSnap,
    SettlementItemSnap,
    SystemSnapshot,
)

if TYPE_CHECKING:
    from tests.support.fake_provider import FakeProvider

ORG = "org-1"
ENV = "env-1"
OTHER_ENV = "env-2"
MERCHANT = "mrc-1"
PROVIDER_ACCOUNT = "acct_relaypay_demo"

CLEARING = "acct-clearing"
PENDING = "acct-pending"
AVAILABLE = "acct-available"
PAYOUT_CLEARING = "acct-payout-clearing"
RECEIVABLE = "acct-receivable"

# Invariants 13 and 15 are only evaluated when the caller says the system is quiesced.
QUIESCED_ONLY = frozenset({InvariantCode.UNKNOWN_PROVIDER_EFFECT, InvariantCode.NOT_QUIESCED})


def _sha(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def _merge(*parts: SystemSnapshot) -> SystemSnapshot:
    """Concatenate every collection of ``parts`` field by field."""
    return SystemSnapshot(
        **{
            field.name: tuple(row for part in parts for row in getattr(part, field.name))
            for field in fields(SystemSnapshot)
        }
    )


def _update[T](rows: tuple[T, ...], match: Callable[[T], bool], **changes: Any) -> tuple[T, ...]:
    """Return ``rows`` with ``changes`` applied (``dataclasses.replace``) to the matching rows."""
    return tuple(replace(row, **changes) if match(row) else row for row in rows)  # type: ignore[type-var]


# ----- row builders -------------------------------------------------------------------------


def _account(
    account_id: str, code: str, account_type: str, merchant: str | None = MERCHANT
) -> LedgerAccountSnap:
    return LedgerAccountSnap(account_id, code, account_type, "INR", ORG, ENV, merchant)


def _journal(
    journal_id: str,
    journal_type: str,
    reference_type: str,
    reference_id: str,
    operation_id: str | None,
) -> JournalSnap:
    return JournalSnap(
        journal_id, f"public_{journal_id}", journal_type, reference_type, reference_id, "INR",
        ORG, ENV, MERCHANT, operation_id,
    )  # fmt: skip


def _operation(
    op: str,
    payment: str,
    resource_type: str,
    resource_id: str,
    kind: str,
    key: str,
    status: str = "SUCCEEDED",
    review_reason: str | None = None,
) -> ProviderOperationSnap:
    terminal = status in {"SUCCEEDED", "FAILED"}
    return ProviderOperationSnap(
        op, f"public_{op}", payment, resource_type, resource_id, kind, key, status,
        review_reason, _sha(f"terminal:{op}") if terminal else None,
    )  # fmt: skip


def _history(op: str, *steps: tuple[str | None, str]) -> tuple[OperationHistorySnap, ...]:
    return tuple(
        OperationHistorySnap(op, ordinal, from_status, to_status, f"STEP_{ordinal}")
        for ordinal, (from_status, to_status) in enumerate(steps)
    )


def _history_for(status: str, op: str) -> tuple[OperationHistorySnap, ...]:
    """Creation is not recorded: PROCESSING has no rows, any other state one row from it."""
    if status == "PROCESSING":
        return ()
    return _history(op, ("PROCESSING", status))


def _idempotency(op: str, *, terminal: bool = True) -> IdempotencyRecordSnap:
    return IdempotencyRecordSnap(
        f"idem_{op}", op, f"key-{op}", terminal, _sha(f"terminal:{op}") if terminal else None
    )


def _effect(
    kind: str, key: str, amount: int, reference: str, status: str = "SUCCEEDED"
) -> ProviderEffectSnap:
    return ProviderEffectSnap(key, kind, amount, status, reference, PROVIDER_ACCOUNT)


def _payment(pid: str, amount: int) -> PaymentIntentSnap:
    return PaymentIntentSnap(pid, f"public_{pid}", ORG, ENV, MERCHANT, amount, "INR")


def _authorized(payment: str, n: str, amount: int) -> SystemSnapshot:
    """An authorized payment: intent excluded; authorization, operation, history, key, effect."""
    op = f"op_a{n}"
    return SystemSnapshot(
        authorizations=(
            AuthorizationSnap(
                f"auth_{n}", f"public_auth_{n}", payment, op, amount, "SUCCEEDED"
            ),
        ),
        provider_operations=(
            _operation(
                op, payment, "AUTHORIZATION", f"auth_{n}", "AUTHORIZE", f"authorize:{payment}"
            ),
        ),
        operation_history=_history_for("SUCCEEDED", op),
        idempotency_records=(_idempotency(op),),
        provider_effects=(
            _effect("AUTHORIZE", f"authorize:{payment}", amount, f"public_auth_{n}"),
        ),
    )  # fmt: skip


def _captured(payment: str, n: str, amount: int) -> SystemSnapshot:
    """A SUCCEEDED capture with its journal (clearing -> pending) and balance transaction."""
    op, journal = f"op_c{n}", f"jrn_c{n}"
    return SystemSnapshot(
        captures=(
            CaptureSnap(
                f"cap_{n}",
                f"public_cap_{n}",
                payment,
                f"auth_{n}",
                op,
                amount,
                "SUCCEEDED",
                journal,
            ),
        ),
        provider_operations=(
            _operation(op, payment, "CAPTURE", f"cap_{n}", "CAPTURE", f"capture:{payment}"),
        ),
        operation_history=_history_for("SUCCEEDED", op),
        idempotency_records=(_idempotency(op),),
        provider_effects=(_effect("CAPTURE", f"capture:{payment}", amount, f"public_cap_{n}"),),
        journals=(_journal(journal, "CAPTURE", "CAPTURE", f"cap_{n}", op),),
        postings=(
            PostingSnap(journal, CLEARING, "DEBIT", amount),
            PostingSnap(journal, PENDING, "CREDIT", amount),
        ),
        balance_transactions=(
            BalanceTransactionSnap(f"btx_{journal}", journal, MERCHANT, "CAPTURE", amount, 0, 0, 0),
        ),
    )  # fmt: skip


def _refunded(
    payment: str, n: str, capture: str, amount: int, *, debit_account: str = PENDING
) -> SystemSnapshot:
    """A SUCCEEDED refund funded from ``debit_account`` (pending payable or merchant receivable)."""
    op, journal = f"op_r{n}", f"jrn_r{n}"
    pending, receivable = (
        (-amount, 0) if debit_account == PENDING else (0, amount)
    )  # fmt: skip
    return SystemSnapshot(
        refunds=(
            RefundSnap(
                f"ref_{n}", f"public_ref_{n}", payment, capture, op, amount, "SUCCEEDED", journal
            ),
        ),
        provider_operations=(
            _operation(op, payment, "REFUND", f"ref_{n}", "REFUND", f"refund:ref_{n}"),
        ),
        operation_history=_history_for("SUCCEEDED", op),
        idempotency_records=(_idempotency(op),),
        provider_effects=(_effect("REFUND", f"refund:ref_{n}", amount, f"public_ref_{n}"),),
        journals=(_journal(journal, "REFUND", "REFUND", f"ref_{n}", op),),
        postings=(
            PostingSnap(journal, debit_account, "DEBIT", amount),
            PostingSnap(journal, CLEARING, "CREDIT", amount),
        ),
        balance_transactions=(
            BalanceTransactionSnap(
                f"btx_{journal}", journal, MERCHANT, "REFUND", pending, 0, receivable, 0
            ),
        ),
    )  # fmt: skip


# ----- the golden world ---------------------------------------------------------------------


def golden_snapshot() -> SystemSnapshot:
    """A consistent world: ``check_invariants`` must return ``[]`` for it, quiesced or not."""
    settlement_journal = _journal("jrn_s2", "SETTLEMENT", "CAPTURE", "cap_2", None)
    base = SystemSnapshot(
        ledger_accounts=(
            _account(CLEARING, "PROVIDER_CLEARING_ASSET", "ASSET", merchant=None),
            _account(PENDING, "PENDING_PAYABLE_LIABILITY", "LIABILITY"),
            _account(AVAILABLE, "AVAILABLE_PAYABLE_LIABILITY", "LIABILITY"),
            _account(PAYOUT_CLEARING, "PAYOUT_CLEARING_ASSET", "ASSET"),
            _account(RECEIVABLE, "MERCHANT_RECEIVABLE_ASSET", "ASSET"),
        ),
        payment_intents=(_payment("pi_1", 100_000), _payment("pi_2", 50_000)),
        # Settlement of cap_2: pending -> available (no provider operation, no effect).
        journals=(settlement_journal,),
        postings=(
            PostingSnap("jrn_s2", PENDING, "DEBIT", 50_000),
            PostingSnap("jrn_s2", AVAILABLE, "CREDIT", 50_000),
        ),
        balance_transactions=(
            BalanceTransactionSnap(
                "btx_jrn_s2", "jrn_s2", MERCHANT, "SETTLEMENT", -50_000, 50_000, 0, 0
            ),
        ),
        settlement_items=(SettlementItemSnap("si_2", "cap_2", "jrn_s2", 50_000),),
        payouts=(PayoutSnap("po_1", "public_po_1", MERCHANT, 20_000, "PROCESSING", None),),
        payout_reservations=(PayoutReservationSnap("po_1", MERCHANT, 1, "RESERVED", 20_000),),
        merchant_balances=(
            MerchantBalanceSnap(MERCHANT, ORG, ENV, 70_000, 50_000, 0, 20_000, 30_000),
        ),
        acked_ops=(
            AckedOp("AUTHORIZE", "public_auth_2", "key-op_a2", 50_000),
            AckedOp("CAPTURE", "public_cap_1", "key-op_c1", 100_000),
            AckedOp("REFUND", "public_ref_1", "key-op_r1", 30_000),
        ),
    )  # fmt: skip
    return _merge(
        base,
        _authorized("pi_1", "1", 100_000),
        _captured("pi_1", "1", 100_000),
        _refunded("pi_1", "1", "cap_1", 30_000),
        _authorized("pi_2", "2", 50_000),
        _captured("pi_2", "2", 50_000),
    )


# ----- in-flight variants (legal while not quiesced) -----------------------------------------


def with_in_flight_operation(snapshot: SystemSnapshot) -> SystemSnapshot:
    """Add ``pi_3`` whose capture is PROCESSING and whose provider effect already committed."""
    return _merge(
        snapshot,
        SystemSnapshot(payment_intents=(_payment("pi_3", 10_000),)),
        _authorized("pi_3", "3", 10_000),
        _in_flight_capture(status="PROCESSING", review_reason=None),
    )


def with_operation_in_review(snapshot: SystemSnapshot) -> SystemSnapshot:
    """Like ``with_in_flight_operation`` but the capture rests in REQUIRES_REVIEW with a reason."""
    return _merge(
        snapshot,
        SystemSnapshot(payment_intents=(_payment("pi_3", 10_000),)),
        _authorized("pi_3", "3", 10_000),
        _in_flight_capture(status="REQUIRES_REVIEW", review_reason="PROVIDER_RESPONSE_AMBIGUOUS"),
    )


def _in_flight_capture(*, status: str, review_reason: str | None) -> SystemSnapshot:
    op = "op_c3"
    return SystemSnapshot(
        captures=(
            CaptureSnap("cap_3", "public_cap_3", "pi_3", "auth_3", op, 10_000, status, None),
        ),
        provider_operations=(
            _operation(
                op, "pi_3", "CAPTURE", "cap_3", "CAPTURE", "capture:pi_3", status, review_reason
            ),
        ),
        operation_history=_history_for(status, op),
        idempotency_records=(_idempotency(op, terminal=False),),
        provider_effects=(_effect("CAPTURE", "capture:pi_3", 10_000, "public_cap_3"),),
    )  # fmt: skip


# ----- one single-defect mutation per invariant code ----------------------------------------


def _unbalanced_journals(snapshot: SystemSnapshot) -> SystemSnapshot:
    # Two journals off by equal and opposite amounts, only on the provider-clearing leg, so the
    # aggregate ledger equation and every balance-transaction projection still hold: conservation
    # forces a lone unbalanced journal to break invariant 2 as well.
    postings = _update(
        snapshot.postings,
        lambda p: (p.journal_id, p.account_id) == ("jrn_c1", CLEARING),
        amount=99_000,
    )
    postings = _update(
        postings,
        lambda p: (p.journal_id, p.account_id) == ("jrn_c2", CLEARING),
        amount=51_000,
    )
    return replace(snapshot, postings=postings)


def _ledger_equation(snapshot: SystemSnapshot) -> SystemSnapshot:
    # Every journal still balances, but a liability account sits in another environment, so the
    # (organisation, environment, currency) groups no longer satisfy assets = liabilities.
    return replace(
        snapshot,
        ledger_accounts=_update(
            snapshot.ledger_accounts, lambda a: a.id == AVAILABLE, environment_id=OTHER_ENV
        ),
    )


def _negative_balance(snapshot: SystemSnapshot) -> SystemSnapshot:
    return replace(
        snapshot, merchant_balances=_update(snapshot.merchant_balances, lambda b: True, pending=-1)
    )


def _reserved_exceeds_available(snapshot: SystemSnapshot) -> SystemSnapshot:
    return replace(
        snapshot,
        merchant_balances=_update(
            snapshot.merchant_balances, lambda b: True, reserved=50_001, payout_eligible=0
        ),
        payouts=_update(snapshot.payouts, lambda p: p.id == "po_1", amount=50_001),
        payout_reservations=_update(
            snapshot.payout_reservations, lambda r: r.payout_id == "po_1", amount_delta=50_001
        ),
    )


def _refunds_exceed_captured(snapshot: SystemSnapshot) -> SystemSnapshot:
    # A second SUCCEEDED refund of cap_1 (30_000 + 70_001 > 100_000), funded from the merchant's
    # receivable so no balance goes negative; journal, key, effect and projection all consistent.
    extra = _refunded("pi_1", "2", "cap_1", 70_001, debit_account=RECEIVABLE)
    grown = _merge(snapshot, extra)
    return replace(
        grown, merchant_balances=_update(grown.merchant_balances, lambda b: True, receivable=70_001)
    )


def _captured_exceeds_authorised(snapshot: SystemSnapshot) -> SystemSnapshot:
    # pi_1 captured 100_000 against an authorization of 99_999 (the provider effect agrees).
    return replace(
        snapshot,
        authorizations=_update(snapshot.authorizations, lambda a: a.id == "auth_1", amount=99_999),
        provider_effects=_update(
            snapshot.provider_effects, lambda e: e.stable_key == "authorize:pi_1", amount=99_999
        ),
    )


def _fee_split_mismatch(snapshot: SystemSnapshot) -> SystemSnapshot:
    # A 500 fee is recorded on the capture but the postings still credit the full gross.
    return replace(
        snapshot,
        captures=_update(snapshot.captures, lambda c: c.id == "cap_1", fee_amount=500),
    )


def _duplicate_provider_effect(snapshot: SystemSnapshot) -> SystemSnapshot:
    effect = next(e for e in snapshot.provider_effects if e.stable_key == "capture:pi_2")
    return replace(snapshot, provider_effects=(*snapshot.provider_effects, effect))


def _missing_journal(snapshot: SystemSnapshot) -> SystemSnapshot:
    # cap_2 is SUCCEEDED but names no journal (and jrn_c2 is claimed by nothing).
    return replace(
        snapshot, captures=_update(snapshot.captures, lambda c: c.id == "cap_2", journal_id=None)
    )


def _idempotency_bytes_diverge(snapshot: SystemSnapshot) -> SystemSnapshot:
    return replace(
        snapshot,
        idempotency_records=_update(
            snapshot.idempotency_records,
            lambda r: r.provider_operation_id == "op_c1",
            response_sha256=_sha("some other response"),
        ),
    )


def _status_drift(snapshot: SystemSnapshot) -> SystemSnapshot:
    # op_a1's history walks out of a terminal state (SUCCEEDED -> PROCESSING).
    return replace(
        snapshot,
        operation_history=(
            *snapshot.operation_history,
            OperationHistorySnap("op_a1", 1, "SUCCEEDED", "PROCESSING", "STEP_1"),
        ),
    )


def _projection_drift(snapshot: SystemSnapshot) -> SystemSnapshot:
    return replace(
        snapshot,
        balance_transactions=_update(
            snapshot.balance_transactions, lambda t: t.journal_id == "jrn_c1", pending_delta=99_999
        ),
    )


def _unknown_provider_effect(snapshot: SystemSnapshot) -> SystemSnapshot:
    ghost = _effect("CAPTURE", "capture:pi_ghost", 777, "public_cap_ghost")
    return replace(snapshot, provider_effects=(*snapshot.provider_effects, ghost))


def _acked_op_missing(snapshot: SystemSnapshot) -> SystemSnapshot:
    ghost = AckedOp("CAPTURE", "public_cap_ghost", "key-ghost", 123)
    return replace(snapshot, acked_ops=(*snapshot.acked_ops, ghost))


MUTATIONS: dict[InvariantCode, Callable[[SystemSnapshot], SystemSnapshot]] = {
    InvariantCode.JOURNAL_UNBALANCED: _unbalanced_journals,
    InvariantCode.LEDGER_EQUATION: _ledger_equation,
    InvariantCode.NEGATIVE_BALANCE: _negative_balance,
    InvariantCode.RESERVED_EXCEEDS_AVAILABLE: _reserved_exceeds_available,
    InvariantCode.REFUNDS_EXCEED_CAPTURED: _refunds_exceed_captured,
    InvariantCode.CAPTURED_EXCEEDS_AUTHORISED: _captured_exceeds_authorised,
    InvariantCode.FEE_SPLIT_MISMATCH: _fee_split_mismatch,
    InvariantCode.DUPLICATE_PROVIDER_EFFECT: _duplicate_provider_effect,
    InvariantCode.MISSING_OR_ORPHAN_JOURNAL: _missing_journal,
    InvariantCode.IDEMPOTENCY_BYTES_DIVERGE: _idempotency_bytes_diverge,
    InvariantCode.STATUS_DRIFT: _status_drift,
    InvariantCode.PROJECTION_DRIFT: _projection_drift,
    InvariantCode.UNKNOWN_PROVIDER_EFFECT: _unknown_provider_effect,
    InvariantCode.ACKED_OP_MISSING: _acked_op_missing,
    InvariantCode.NOT_QUIESCED: with_in_flight_operation,
}


def mutated_snapshot(code: InvariantCode) -> SystemSnapshot:
    """Golden with exactly one defect of ``code`` (all of the defect's rows are of that code)."""
    return MUTATIONS[code](golden_snapshot())


def fake_provider_effects(provider: "FakeProvider") -> tuple[ProviderEffectSnap, ...]:
    """The effects a ``FakeProvider`` committed, as snapshot rows (ordered by stable key)."""
    return tuple(
        ProviderEffectSnap(
            stable_key=effect.stable_key,
            kind=effect.operation_kind,
            amount=effect.amount,
            status=effect.outcome,
            reference=effect.reference,
            provider_account_id=provider.account_id,
        )
        for effect in sorted(provider.effects.values(), key=lambda e: e.stable_key)
    )
