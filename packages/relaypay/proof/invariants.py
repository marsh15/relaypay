"""Money invariants over a ``SystemSnapshot`` (see PROVENANCE).

Implemented by Claude at the maintainer's request, against the specification in the
``check_invariants`` docstring and the pre-existing tests in ``tests/unit/test_invariants_spec.py``.
Each invariant is one small function returning its violations; ``check_invariants`` concatenates
them in invariant order, each sorted by subject, so the output is deterministic.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from relaypay.proof.snapshot_types import (
    InvariantCode,
    SystemSnapshot,
    Violation,
)

FEE_ACCOUNT_CODE = "PLATFORM_FEE_REVENUE"
GST_ACCOUNT_CODE = "GST_PAYABLE_LIABILITY"
PENDING_CODE = "PENDING_PAYABLE_LIABILITY"
AVAILABLE_CODE = "AVAILABLE_PAYABLE_LIABILITY"
RECEIVABLE_CODE = "MERCHANT_RECEIVABLE_ASSET"
PAYOUT_CLEARING_CODE = "PAYOUT_CLEARING_ASSET"
MERCHANT_CODES = frozenset({PENDING_CODE, AVAILABLE_CODE, RECEIVABLE_CODE, PAYOUT_CLEARING_CODE})
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED"})
JOURNAL_TYPES_NAMED_BY_RESOURCE = frozenset({"CAPTURE", "REFUND", "PAYOUT"})


def _violation(code: InvariantCode, subject: str, message: str) -> Violation:
    return Violation(code=code.value, subject=subject, message=message)


def _ordered(violations: list[Violation]) -> list[Violation]:
    return sorted(violations, key=lambda violation: (violation.subject, violation.message))


@dataclass(frozen=True, slots=True)
class _Ledger:
    """Postings grouped once, with each account's code, type and merchant looked up."""

    account_code: dict[str, str]
    account_type: dict[str, str]
    account_merchant: dict[str, str | None]
    postings_by_journal: dict[str, list[tuple[str, str, int]]]  # (account_id, side, amount)

    @classmethod
    def of(cls, snapshot: SystemSnapshot) -> "_Ledger":
        by_journal: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for posting in snapshot.postings:
            by_journal[posting.journal_id].append(
                (posting.account_id, posting.side, posting.amount)
            )
        return cls(
            account_code={a.id: a.code for a in snapshot.ledger_accounts},
            account_type={a.id: a.account_type for a in snapshot.ledger_accounts},
            account_merchant={a.id: a.merchant_account_id for a in snapshot.ledger_accounts},
            postings_by_journal=dict(by_journal),
        )

    def credit_minus_debit(
        self, journal_id: str, *, code: str, merchant_account_id: str | None = None
    ) -> int:
        """Net credits on the journal's postings to accounts with this code (and merchant)."""
        total = 0
        for account_id, side, amount in self.postings_by_journal.get(journal_id, []):
            if self.account_code.get(account_id) != code:
                continue
            if merchant_account_id is not None and (
                self.account_merchant.get(account_id) != merchant_account_id
            ):
                continue
            total += amount if side == "CREDIT" else -amount
        return total

    def moves_merchant_account(self, journal_id: str, merchant_account_id: str) -> bool:
        return any(
            self.account_code.get(account_id) in MERCHANT_CODES
            and self.account_merchant.get(account_id) == merchant_account_id
            for account_id, _, _ in self.postings_by_journal.get(journal_id, [])
        )


# 1
def _journal_unbalanced(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    for journal in snapshot.journals:
        postings = ledger.postings_by_journal.get(journal.id, [])
        debits = sum(amount for _, side, amount in postings if side == "DEBIT")
        credits = sum(amount for _, side, amount in postings if side == "CREDIT")
        if len(postings) < 2 or debits != credits:
            found.append(
                _violation(
                    InvariantCode.JOURNAL_UNBALANCED,
                    journal.id,
                    f"journal has {len(postings)} postings, debits {debits}, credits {credits}",
                )
            )
    return found


# 2
def _ledger_equation(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    accounts = {account.id: account for account in snapshot.ledger_accounts}
    difference: dict[tuple[str, str, str], int] = defaultdict(int)
    for posting in snapshot.postings:
        account = accounts.get(posting.account_id)
        if account is None:
            continue
        # Assets are debit-positive; liabilities and income are credit-positive. The equation
        # assets - (liabilities + income) == 0 therefore sums every posting as debit minus credit.
        signed = posting.amount if posting.side == "DEBIT" else -posting.amount
        difference[(account.organisation_id, account.environment_id, account.currency)] += signed
    return [
        _violation(
            InvariantCode.LEDGER_EQUATION,
            ":".join(group),
            f"assets minus liabilities and income is {amount}, expected 0",
        )
        for group, amount in difference.items()
        if amount != 0
    ]


# 3
def _negative_balance(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    for balance in snapshot.merchant_balances:
        for name, value in (
            ("pending", balance.pending),
            ("available", balance.available),
            ("receivable", balance.receivable),
        ):
            if value < 0:
                found.append(
                    _violation(
                        InvariantCode.NEGATIVE_BALANCE,
                        balance.merchant_account_id,
                        f"{name} balance is {value}",
                    )
                )
    return found


# 4
def _reserved_exceeds_available(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    return [
        _violation(
            InvariantCode.RESERVED_EXCEEDS_AVAILABLE,
            balance.merchant_account_id,
            f"reserved {balance.reserved} exceeds available {balance.available}",
        )
        for balance in snapshot.merchant_balances
        if balance.reserved > balance.available
    ]


# 5
def _refunds_exceed_captured(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    refunded: dict[str, int] = defaultdict(int)
    for refund in snapshot.refunds:
        if refund.status == "SUCCEEDED":
            refunded[refund.capture_id] += refund.amount
    return [
        _violation(
            InvariantCode.REFUNDS_EXCEED_CAPTURED,
            capture.id,
            f"succeeded refunds {refunded[capture.id]} exceed capture {capture.amount}",
        )
        for capture in snapshot.captures
        if refunded[capture.id] > capture.amount
    ]


# 6
def _captured_exceeds_authorised(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    captured: dict[str, int] = defaultdict(int)
    authorised: dict[str, int] = defaultdict(int)
    for capture in snapshot.captures:
        if capture.status == "SUCCEEDED":
            captured[capture.payment_intent_id] += capture.amount
    for authorization in snapshot.authorizations:
        if authorization.status == "SUCCEEDED":
            authorised[authorization.payment_intent_id] += authorization.amount
    return [
        _violation(
            InvariantCode.CAPTURED_EXCEEDS_AUTHORISED,
            payment_id,
            f"succeeded captures {amount} exceed authorised {authorised[payment_id]}",
        )
        for payment_id, amount in captured.items()
        if amount > authorised[payment_id]
    ]


# 7
def _fee_split_mismatch(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    for capture in snapshot.captures:
        if capture.status != "SUCCEEDED" or capture.journal_id is None:
            continue
        net = capture.amount - capture.fee_amount - capture.gst_amount
        problems: list[str] = []
        if capture.fee_amount < 0 or capture.gst_amount < 0 or net < 0:
            problems.append("fee, GST or net is negative")
        merchant = next(
            (
                p.merchant_account_id
                for p in snapshot.payment_intents
                if p.id == capture.payment_intent_id
            ),
            None,
        )
        posted = {
            "net": ledger.credit_minus_debit(
                capture.journal_id, code=PENDING_CODE, merchant_account_id=merchant
            ),
            "fee": ledger.credit_minus_debit(capture.journal_id, code=FEE_ACCOUNT_CODE),
            "gst": ledger.credit_minus_debit(capture.journal_id, code=GST_ACCOUNT_CODE),
        }
        for name, expected in (
            ("net", net),
            ("fee", capture.fee_amount),
            ("gst", capture.gst_amount),
        ):
            if posted[name] != expected:
                problems.append(f"{name} posted {posted[name]}, expected {expected}")
        if problems:
            found.append(
                _violation(InvariantCode.FEE_SPLIT_MISMATCH, capture.id, "; ".join(problems))
            )
    return found


# 8
def _duplicate_provider_effect(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    effects: dict[tuple[str, str], int] = defaultdict(int)
    for effect in snapshot.provider_effects:
        effects[(effect.provider_account_id, effect.stable_key)] += 1
    found.extend(
        _violation(
            InvariantCode.DUPLICATE_PROVIDER_EFFECT,
            stable_key,
            f"{count} provider effects share the stable key",
        )
        for (_, stable_key), count in effects.items()
        if count > 1
    )
    journals: dict[tuple[str, str], int] = defaultdict(int)
    for journal in snapshot.journals:
        if journal.journal_type in {"CAPTURE", "REFUND"}:
            journals[(journal.journal_type, journal.reference_id)] += 1
    found.extend(
        _violation(
            InvariantCode.DUPLICATE_PROVIDER_EFFECT,
            reference_id,
            f"{count} {journal_type} journals reference the same resource",
        )
        for (journal_type, reference_id), count in journals.items()
        if count > 1
    )
    return found


# 9
def _missing_or_orphan_journal(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    journal_ids = {journal.id for journal in snapshot.journals}
    claims: dict[str, int] = defaultdict(int)
    resources: list[tuple[str, str, str | None]] = [
        *((c.id, c.status, c.journal_id) for c in snapshot.captures),
        *((r.id, r.status, r.journal_id) for r in snapshot.refunds),
        *((p.id, p.status, p.journal_id) for p in snapshot.payouts),
    ]
    for resource_id, status, journal_id in resources:
        if journal_id is not None:
            claims[journal_id] += 1
        if status == "SUCCEEDED" and (journal_id is None or journal_id not in journal_ids):
            found.append(
                _violation(
                    InvariantCode.MISSING_OR_ORPHAN_JOURNAL,
                    resource_id,
                    "succeeded resource has no existing journal",
                )
            )
    for item in snapshot.settlement_items:
        claims[item.journal_id] += 1
    for journal in snapshot.journals:
        named_by_resource = journal.journal_type in JOURNAL_TYPES_NAMED_BY_RESOURCE
        if (named_by_resource or journal.journal_type == "SETTLEMENT") and claims[journal.id] != 1:
            found.append(
                _violation(
                    InvariantCode.MISSING_OR_ORPHAN_JOURNAL,
                    journal.id,
                    f"{journal.journal_type} journal is claimed by {claims[journal.id]} resources",
                )
            )
    return found


# 10
def _idempotency_bytes_diverge(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    terminal = {op.id: op.terminal_response_sha256 for op in snapshot.provider_operations}
    return [
        _violation(
            InvariantCode.IDEMPOTENCY_BYTES_DIVERGE,
            record.id,
            "idempotent response bytes differ from the operation's terminal response",
        )
        for record in snapshot.idempotency_records
        if record.is_terminal
        and record.provider_operation_id in terminal
        and record.response_sha256 != terminal[record.provider_operation_id]
    ]


def _history_defect(status: str, rows: list[tuple[int, str | None, str]]) -> str | None:
    """Why an operation's recorded walk is illegal, or ``None`` when it is a legal walk."""
    if not rows:
        return None if status == "PROCESSING" else f"{status} operation has no history"
    previous = "PROCESSING"
    for index, (_, from_status, to_status) in enumerate(rows):
        if from_status != previous:
            return f"transition {index} starts from {from_status}, expected {previous}"
        if previous in TERMINAL_STATUSES:
            return f"transition {index} leaves terminal status {previous}"
        previous = to_status
    if previous != status:
        return f"history ends in {previous} but the operation is {status}"
    return None


def _allowed_payment_statuses(
    snapshot: SystemSnapshot,
) -> Callable[[str], set[str]]:
    """Statuses a payment may hold given its authorizations, captures and refunds."""
    authorised: dict[str, set[str]] = defaultdict(set)
    captured: dict[str, int] = defaultdict(int)
    refunded: dict[str, int] = defaultdict(int)
    for authorization in snapshot.authorizations:
        authorised[authorization.payment_intent_id].add(authorization.status)
    for capture in snapshot.captures:
        if capture.status == "SUCCEEDED":
            captured[capture.payment_intent_id] += capture.amount
    for refund in snapshot.refunds:
        if refund.status == "SUCCEEDED":
            refunded[refund.payment_intent_id] += refund.amount

    def allowed(payment_id: str) -> set[str]:
        if refunded[payment_id] > 0:
            full = refunded[payment_id] >= captured[payment_id]
            return {"refunded"} if full else {"partially_refunded"}
        if captured[payment_id] > 0:
            return {"captured"}
        statuses = authorised[payment_id]
        if "SUCCEEDED" in statuses:
            return {"authorised", "failed", "expired", "cancelled"}
        if statuses == {"FAILED"}:
            return {"failed"}
        return {"created", "requires_action", "failed", "expired", "cancelled"}

    return allowed


# 11
def _status_drift(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    history: dict[str, list[tuple[int, str | None, str]]] = defaultdict(list)
    for row in snapshot.operation_history:
        history[row.provider_operation_id].append((row.ordinal, row.from_status, row.to_status))
    for operation in snapshot.provider_operations:
        defect = _history_defect(
            operation.status, sorted(history[operation.id], key=lambda r: r[0])
        )
        if defect is not None:
            found.append(_violation(InvariantCode.STATUS_DRIFT, operation.id, defect))
    allowed = _allowed_payment_statuses(snapshot)
    for payment in snapshot.payment_intents:
        if payment.status is not None and payment.status not in allowed(payment.id):
            found.append(
                _violation(
                    InvariantCode.STATUS_DRIFT,
                    payment.id,
                    f"payment is {payment.status}, resources allow {sorted(allowed(payment.id))}",
                )
            )
    return found


# 12
def _projection_drift(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    per_journal: dict[str, int] = defaultdict(int)
    for transaction in snapshot.balance_transactions:
        per_journal[transaction.journal_id] += 1
        merchant = transaction.merchant_account_id
        expected = {
            "pending": ledger.credit_minus_debit(
                transaction.journal_id, code=PENDING_CODE, merchant_account_id=merchant
            ),
            "available": ledger.credit_minus_debit(
                transaction.journal_id, code=AVAILABLE_CODE, merchant_account_id=merchant
            ),
            "receivable": -ledger.credit_minus_debit(
                transaction.journal_id, code=RECEIVABLE_CODE, merchant_account_id=merchant
            ),
            "payout clearing": ledger.credit_minus_debit(
                transaction.journal_id, code=PAYOUT_CLEARING_CODE, merchant_account_id=merchant
            ),
        }
        actual = {
            "pending": transaction.pending_delta,
            "available": transaction.available_delta,
            "receivable": transaction.receivable_delta,
            "payout clearing": transaction.payout_clearing_delta,
        }
        differing = [
            f"{name} delta {actual[name]} expected {expected[name]}"
            for name in expected
            if actual[name] != expected[name]
        ]
        if differing:
            found.append(
                _violation(InvariantCode.PROJECTION_DRIFT, transaction.id, "; ".join(differing))
            )
    merchants = {t.merchant_account_id for t in snapshot.balance_transactions} | {
        a for a in (ledger.account_merchant.values()) if a is not None
    }
    for journal in snapshot.journals:
        moved = any(ledger.moves_merchant_account(journal.id, m) for m in merchants)
        if moved and per_journal[journal.id] != 1:
            found.append(
                _violation(
                    InvariantCode.PROJECTION_DRIFT,
                    journal.id,
                    f"journal moves a merchant account and has {per_journal[journal.id]} "
                    "balance transactions",
                )
            )
    return found


# 13
def _unknown_provider_effect(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    known = {operation.stable_provider_key for operation in snapshot.provider_operations}
    unknown = {e.stable_key for e in snapshot.provider_effects if e.stable_key not in known}
    return [
        _violation(
            InvariantCode.UNKNOWN_PROVIDER_EFFECT,
            stable_key,
            "provider effect has no matching provider operation",
        )
        for stable_key in unknown
    ]


# 14
def _acked_op_missing(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    resources: dict[str, dict[str, int]] = {
        "AUTHORIZE": {a.public_id: a.amount for a in snapshot.authorizations},
        "CAPTURE": {c.public_id: c.amount for c in snapshot.captures},
        "REFUND": {r.public_id: r.amount for r in snapshot.refunds},
    }
    hints = {record.key_hint for record in snapshot.idempotency_records}
    found: list[Violation] = []
    for acked in snapshot.acked_ops:
        amounts = resources.get(acked.kind, {})
        if amounts.get(acked.resource_id) != acked.amount:
            reason = "no resource with that id and amount"
        elif acked.idempotency_key_hint not in hints:
            reason = "no idempotency record carries the key hint"
        else:
            continue
        found.append(_violation(InvariantCode.ACKED_OP_MISSING, acked.resource_id, reason))
    return found


# 15
def _not_quiesced(snapshot: SystemSnapshot, ledger: _Ledger) -> list[Violation]:
    found: list[Violation] = []
    for operation in snapshot.provider_operations:
        if operation.status == "PROCESSING":
            found.append(
                _violation(
                    InvariantCode.NOT_QUIESCED, operation.id, "operation is still PROCESSING"
                )
            )
        elif operation.status == "REQUIRES_REVIEW" and not operation.review_reason:
            found.append(
                _violation(
                    InvariantCode.NOT_QUIESCED,
                    operation.id,
                    "operation awaits review with no reason",
                )
            )
    return found


Check = Callable[[SystemSnapshot, _Ledger], list[Violation]]
# In invariant order; the flag marks the checks evaluated only when quiesced (13 and 15).
CHECKS: tuple[tuple[Check, bool], ...] = (
    (_journal_unbalanced, False),
    (_ledger_equation, False),
    (_negative_balance, False),
    (_reserved_exceeds_available, False),
    (_refunds_exceed_captured, False),
    (_captured_exceeds_authorised, False),
    (_fee_split_mismatch, False),
    (_duplicate_provider_effect, False),
    (_missing_or_orphan_journal, False),
    (_idempotency_bytes_diverge, False),
    (_status_drift, False),
    (_projection_drift, False),
    (_unknown_provider_effect, True),
    (_acked_op_missing, False),
    (_not_quiesced, True),
)


def check_invariants(snapshot: SystemSnapshot, *, quiesced: bool) -> list[Violation]:
    """Return every violated invariant in ``snapshot`` (``[]`` means the system is sound).

    Pure function of its arguments; it must never touch a database. Output is deterministic: the
    same snapshot yields the same list in the same order. Each ``Violation.code`` is the
    ``InvariantCode`` value named below, ``subject`` identifies the offending row (an id or a
    stable key) and ``message`` is human-readable. A single defect should be reported under the
    one code that names it and under no other code.

    ``quiesced`` is True only when the caller guarantees no request is in flight and every
    recovery worker has drained; invariants 13 and 15 are evaluated only then. All other
    invariants hold at every instant, including mid-flight, and must not flag legitimately
    in-flight (PROCESSING) work.

    Conventions. Amounts are integer paise. ``side`` is DEBIT or CREDIT. An ASSET account's
    balance is debits minus credits; a LIABILITY (and INCOME) account's balance is credits minus
    debits. "Terminal" operation status is SUCCEEDED or FAILED; PROCESSING and REQUIRES_REVIEW
    are non-terminal. ``PaymentIntentSnap.status`` is None until M16 adds the column; when it is
    None skip the status-versus-derived-view half of invariant 11 and still check the history.

    The invariants (one code each):

    1. JOURNAL_UNBALANCED. Every journal has at least 2 postings, and the sum of its DEBIT
       posting amounts equals the sum of its CREDIT posting amounts. One violation per bad
       journal (subject: journal id).

    2. LEDGER_EQUATION. For each (organisation_id, environment_id, currency) taken from the
       posting's ledger account, the sum of ASSET balances equals the sum of LIABILITY balances
       plus INCOME balances: assets - (liabilities + income) == 0. One violation per offending
       group (subject: "organisation:environment:currency").

    3. NEGATIVE_BALANCE. For every ``MerchantBalanceSnap`` (taken as given, not recomputed from
       postings), pending, available and receivable are each >= 0. One violation per merchant
       account and field (subject: merchant account id).

    4. RESERVED_EXCEEDS_AVAILABLE. For every ``MerchantBalanceSnap``, reserved <= available
       (subject: merchant account id).

    5. REFUNDS_EXCEED_CAPTURED. For every capture, the sum of its SUCCEEDED refunds is at most
       the capture's amount (subject: capture id). In-flight refunds do not count.

    6. CAPTURED_EXCEEDS_AUTHORISED. For every payment intent, the sum of its SUCCEEDED captures
       is at most the amount of its SUCCEEDED authorization (subject: payment intent id).

    7. FEE_SPLIT_MISMATCH. For every SUCCEEDED capture, fee_amount + gst_amount + net ==
       gross (``amount``), with fee_amount >= 0, gst_amount >= 0 and net >= 0, and the
       capture's journal postings agree: the merchant's PENDING_PAYABLE_LIABILITY credit equals
       net, and any fee and GST postings equal fee_amount and gst_amount (subject: capture id).
       Until M16, fee_amount == gst_amount == 0 and net == gross. A SUCCEEDED capture with no
       journal is reported only under invariant 9, not here.

    8. DUPLICATE_PROVIDER_EFFECT. At most one ``ProviderEffectSnap`` per (provider_account_id,
       stable_key) (subject: stable key), and at most one journal per (journal_type,
       reference_id) for CAPTURE and REFUND journals (subject: reference id).

    9. MISSING_OR_ORPHAN_JOURNAL. Every SUCCEEDED capture and SUCCEEDED refund (and SUCCEEDED
       payout) names a journal that exists, and every CAPTURE, REFUND and PAYOUT journal is
       named by exactly one such resource (SETTLEMENT journals by a settlement item). Violations:
       a SUCCEEDED resource without a journal, or a journal no resource claims (subject: the
       resource id or the journal id).

    10. IDEMPOTENCY_BYTES_DIVERGE. For every idempotency record that is_terminal, its
        response_sha256 equals the terminal_response_sha256 of the record's provider operation
        (subject: idempotency record id). Non-terminal records are not compared.

    11. STATUS_DRIFT. (a) Each operation's history is a legal walk. Creation is not recorded,
        so an operation starts in PROCESSING and an operation with no history rows must have
        status PROCESSING. Otherwise, taking its rows in ordinal order: the first row's
        from_status is PROCESSING, every later row's from_status equals the previous row's
        to_status, SUCCEEDED and FAILED have no outgoing row, and the last to_status equals the
        operation's current status. (b) When ``PaymentIntentSnap.status`` is not None it equals
        the status derived from the payment's authorizations, captures and refunds.
        (subject: operation id or payment intent id).

    12. PROJECTION_DRIFT. For every balance transaction, its deltas equal the posting effect of
        its journal on the merchant's ledger accounts: pending_delta = credits - debits on
        PENDING_PAYABLE_LIABILITY, available_delta = credits - debits on
        AVAILABLE_PAYABLE_LIABILITY, receivable_delta = debits - credits on
        MERCHANT_RECEIVABLE_ASSET, payout_clearing_delta = credits - debits on
        PAYOUT_CLEARING_ASSET (note the credit-positive sign). Every journal that moves a
        merchant account has exactly one balance transaction (subject: balance transaction id
        or journal id).

    13. UNKNOWN_PROVIDER_EFFECT. Only when ``quiesced``: every ``ProviderEffectSnap`` matches a
        provider operation by stable_key (subject: stable key). When not quiesced an effect with
        no operation yet is tolerated.

    14. ACKED_OP_MISSING. Every ``AckedOp`` corresponds to a resource in the snapshot of the
        same kind with the same public id and amount, and to an idempotency record whose
        key_hint equals ``idempotency_key_hint`` (subject: resource id).

    15. NOT_QUIESCED. Only when ``quiesced``: no provider operation is PROCESSING, and none is
        REQUIRES_REVIEW without a ``review_reason`` (subject: operation id). REQUIRES_REVIEW with
        a reason is an accepted resting state.
    """
    ledger = _Ledger.of(snapshot)
    found: list[Violation] = []
    for check, quiesced_only in CHECKS:
        if quiesced or not quiesced_only:
            found.extend(_ordered(check(snapshot, ledger)))
    return found
