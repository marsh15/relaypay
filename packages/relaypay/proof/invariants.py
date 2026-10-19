"""Money invariants over a ``SystemSnapshot``. HAND-WRITTEN BY THE MAINTAINER (see PROVENANCE).

This file is a typed stub: the signature and the specification below are committed, the
implementation is not. The red tests in ``tests/unit/test_invariants_spec.py`` are written from
this specification.
"""

from relaypay.proof.snapshot_types import SystemSnapshot, Violation


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
    raise NotImplementedError("hand-written by the maintainer; see PROVENANCE")
