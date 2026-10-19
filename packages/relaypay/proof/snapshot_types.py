"""Frozen, hashable value types for the proof harness.

A ``SystemSnapshot`` holds everything the invariant checker needs, so checking never touches a
database. Every collection is a tuple (hashable, deterministic order) and every identifier is the
string form of the row's UUID unless it is named ``public_id``. Amounts are integer paise.
Optional fields that M16 will populate (payment status, capture fee and GST) default to
``None`` / ``0`` so the M15 snapshot of today's schema is already complete.
"""

from dataclasses import dataclass
from enum import StrEnum


class InvariantCode(StrEnum):
    """One member per invariant in the v2.0 plan (numbered as in ``check_invariants``)."""

    JOURNAL_UNBALANCED = "JOURNAL_UNBALANCED"  # 1
    LEDGER_EQUATION = "LEDGER_EQUATION"  # 2
    NEGATIVE_BALANCE = "NEGATIVE_BALANCE"  # 3
    RESERVED_EXCEEDS_AVAILABLE = "RESERVED_EXCEEDS_AVAILABLE"  # 4
    REFUNDS_EXCEED_CAPTURED = "REFUNDS_EXCEED_CAPTURED"  # 5
    CAPTURED_EXCEEDS_AUTHORISED = "CAPTURED_EXCEEDS_AUTHORISED"  # 6
    FEE_SPLIT_MISMATCH = "FEE_SPLIT_MISMATCH"  # 7
    DUPLICATE_PROVIDER_EFFECT = "DUPLICATE_PROVIDER_EFFECT"  # 8
    MISSING_OR_ORPHAN_JOURNAL = "MISSING_OR_ORPHAN_JOURNAL"  # 9
    IDEMPOTENCY_BYTES_DIVERGE = "IDEMPOTENCY_BYTES_DIVERGE"  # 10
    STATUS_DRIFT = "STATUS_DRIFT"  # 11
    PROJECTION_DRIFT = "PROJECTION_DRIFT"  # 12
    UNKNOWN_PROVIDER_EFFECT = "UNKNOWN_PROVIDER_EFFECT"  # 13 (quiesced only)
    ACKED_OP_MISSING = "ACKED_OP_MISSING"  # 14
    NOT_QUIESCED = "NOT_QUIESCED"  # 15 (quiesced only)


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken invariant: a stable ``code`` (an ``InvariantCode`` value), the offending
    ``subject`` (an id or stable key) and a human-readable ``message``."""

    code: str
    subject: str
    message: str


@dataclass(frozen=True, slots=True)
class AckedOp:
    """An operation the client (chaos driver) saw acknowledged.

    ``kind`` is AUTHORIZE, CAPTURE or REFUND; ``resource_id`` the public id of the
    authorization, capture or refund named in the response (``resourceId``);
    ``idempotency_key_hint`` the ``key_hint`` of the Idempotency-Key it was sent with.
    """

    kind: str
    resource_id: str
    idempotency_key_hint: str
    amount: int


@dataclass(frozen=True, slots=True)
class LedgerAccountSnap:
    id: str
    code: str
    account_type: str  # ASSET | LIABILITY (INCOME once the fee ledger lands)
    currency: str
    organisation_id: str
    environment_id: str
    merchant_account_id: str | None


@dataclass(frozen=True, slots=True)
class JournalSnap:
    id: str
    public_id: str
    journal_type: str  # CAPTURE | REFUND | COMPENSATION | OPENING | SETTLEMENT | PAYOUT
    reference_type: str
    reference_id: str
    currency: str
    organisation_id: str
    environment_id: str
    merchant_account_id: str | None
    provider_operation_id: str | None


@dataclass(frozen=True, slots=True)
class PostingSnap:
    journal_id: str
    account_id: str
    side: str  # DEBIT | CREDIT
    amount: int  # > 0


@dataclass(frozen=True, slots=True)
class MerchantBalanceSnap:
    """``derive_balances`` output for one merchant account, taken inside the snapshot txn."""

    merchant_account_id: str
    organisation_id: str
    environment_id: str
    pending: int
    available: int
    receivable: int
    reserved: int
    payout_eligible: int


@dataclass(frozen=True, slots=True)
class PayoutSnap:
    id: str
    public_id: str
    merchant_account_id: str
    amount: int
    status: str  # PROCESSING | REQUIRES_REVIEW | SUCCEEDED | FAILED
    journal_id: str | None


@dataclass(frozen=True, slots=True)
class PayoutReservationSnap:
    """One ``payout_reservation_history`` row; reserved = sum of ``amount_delta``."""

    payout_id: str
    merchant_account_id: str
    sequence: int
    action: str  # RESERVED | RELEASED | CONSUMED
    amount_delta: int


@dataclass(frozen=True, slots=True)
class SettlementItemSnap:
    id: str
    capture_id: str
    journal_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class PaymentIntentSnap:
    id: str
    public_id: str
    organisation_id: str
    environment_id: str
    merchant_account_id: str
    amount: int
    currency: str
    status: str | None = None  # None until M16 adds payment_intents.status


@dataclass(frozen=True, slots=True)
class AuthorizationSnap:
    id: str
    public_id: str
    payment_intent_id: str
    provider_operation_id: str
    amount: int
    status: str  # PROCESSING | SUCCEEDED | FAILED | REQUIRES_REVIEW
    journal_id: str | None = None  # authorizations post no journal


@dataclass(frozen=True, slots=True)
class CaptureSnap:
    id: str
    public_id: str
    payment_intent_id: str
    authorization_id: str
    provider_operation_id: str
    amount: int  # gross
    status: str
    journal_id: str | None
    fee_amount: int = 0  # populated by M16
    gst_amount: int = 0  # populated by M16


@dataclass(frozen=True, slots=True)
class RefundSnap:
    id: str
    public_id: str
    payment_intent_id: str
    capture_id: str
    provider_operation_id: str
    amount: int
    status: str
    journal_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderOperationSnap:
    id: str
    public_id: str
    payment_intent_id: str
    resource_type: str  # AUTHORIZATION | CAPTURE | REFUND
    resource_id: str
    kind: str  # AUTHORIZE | CAPTURE | REFUND
    stable_provider_key: str
    status: str  # PROCESSING | SUCCEEDED | FAILED | REQUIRES_REVIEW
    review_reason: str | None
    terminal_response_sha256: bytes | None


@dataclass(frozen=True, slots=True)
class OperationHistorySnap:
    """One ``operation_history`` transition. ``ordinal`` is the 0-based position of the
    transition within its operation's history, in (created_at, id) order."""

    provider_operation_id: str
    ordinal: int
    from_status: str | None
    to_status: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecordSnap:
    """An idempotency record attached to a provider operation (payment-create records, which
    have no operation, are not captured)."""

    id: str
    provider_operation_id: str
    key_hint: str | None
    is_terminal: bool
    response_sha256: bytes | None


@dataclass(frozen=True, slots=True)
class ProviderEffectSnap:
    """A side effect recorded by the provider (PROVIDER database, or a fake provider's log)."""

    stable_key: str
    kind: str  # AUTHORIZE | CAPTURE | REFUND
    amount: int
    status: str  # PENDING | SUCCEEDED | DECLINED
    reference: str  # public id of the authorization/capture/refund the effect was created for
    provider_account_id: str = ""


@dataclass(frozen=True, slots=True)
class BalanceTransactionSnap:
    id: str
    journal_id: str
    merchant_account_id: str
    transaction_type: str
    pending_delta: int
    available_delta: int
    receivable_delta: int
    payout_clearing_delta: int


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """A consistent view of both databases plus the driver's acknowledged-operation log."""

    ledger_accounts: tuple[LedgerAccountSnap, ...] = ()
    journals: tuple[JournalSnap, ...] = ()
    postings: tuple[PostingSnap, ...] = ()
    merchant_balances: tuple[MerchantBalanceSnap, ...] = ()
    payouts: tuple[PayoutSnap, ...] = ()
    payout_reservations: tuple[PayoutReservationSnap, ...] = ()
    settlement_items: tuple[SettlementItemSnap, ...] = ()
    payment_intents: tuple[PaymentIntentSnap, ...] = ()
    authorizations: tuple[AuthorizationSnap, ...] = ()
    captures: tuple[CaptureSnap, ...] = ()
    refunds: tuple[RefundSnap, ...] = ()
    provider_operations: tuple[ProviderOperationSnap, ...] = ()
    operation_history: tuple[OperationHistorySnap, ...] = ()
    idempotency_records: tuple[IdempotencyRecordSnap, ...] = ()
    provider_effects: tuple[ProviderEffectSnap, ...] = ()
    balance_transactions: tuple[BalanceTransactionSnap, ...] = ()
    acked_ops: tuple[AckedOp, ...] = ()
