"""Capture a ``SystemSnapshot`` from the RelayPay and provider databases.

Read-only by construction: each database is read in one ``REPEATABLE READ, READ ONLY``
transaction, so a snapshot is internally consistent per database even while writers run. The two
databases cannot share a snapshot; RelayPay is read first and the provider second, so an effect
committed between the two reads can appear without its operation. The checker tolerates that
unless the caller states the system is quiesced.

Scoping: ``organisation_ids`` restricts every RelayPay table to those tenants. Provider effects
carry no organisation, so a scoped capture keeps the effects whose stable key or reference
(the public id of the authorization, capture or refund) belongs to the scoped operations. An
effect that matches nothing in scope is therefore invisible to a scoped snapshot.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import replace

from sqlalchemy import ColumnElement, Row, select, text, true
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.merchant_balances.models import (
    BalanceTransaction,
    MerchantAccount,
    SettlementItem,
)
from relaypay.merchant_balances.service import derive_balances
from relaypay.mock_provider.models import ProviderAccount, ProviderEffect
from relaypay.payments.models import (
    Authorization,
    Capture,
    PaymentIntent,
    Refund,
)
from relaypay.payouts.models import Payout, PayoutReservationHistory
from relaypay.proof.snapshot_types import (
    AckedOp,
    AuthorizationSnap,
    BalanceTransactionSnap,
    CaptureSnap,
    IdempotencyRecordSnap,
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
from relaypay.provider_operations.models import (
    IdempotencyRecord,
    OperationHistory,
    ProviderOperation,
)

POSTING_BATCH_SIZE = 2000


def capture_snapshot(
    relaypay_factory: sessionmaker[Session],
    provider_factory: sessionmaker[Session] | None,
    *,
    organisation_ids: Sequence[uuid.UUID] | None = None,
    acked_ops: Sequence[AckedOp] = (),
    extra_provider_effects: Sequence[ProviderEffectSnap] = (),
) -> SystemSnapshot:
    """Read a point-in-time snapshot; never writes.

    ``provider_factory`` may be None when the provider is an in-process fake: pass its effects as
    ``extra_provider_effects`` instead (do not pass the same effects through both; a scoped
    capture filters them like database effects). Output order
    is deterministic for a given database state. ``acked_ops`` is the driver's log of operations
    the client saw acknowledged.
    """
    scope = tuple(organisation_ids) if organisation_ids is not None else None
    with relaypay_factory() as session, session.begin():
        _begin_read_only_snapshot(session)
        relay = _read_relaypay(session, scope)
    effects: list[ProviderEffectSnap] = []
    if provider_factory is not None:
        with provider_factory() as provider_session, provider_session.begin():
            _begin_read_only_snapshot(provider_session)
            effects.extend(_read_provider_effects(provider_session, relay, scope is not None))
    if scope is None:
        effects.extend(extra_provider_effects)
    else:
        keys, references = _scope_keys(relay)
        effects.extend(
            effect
            for effect in extra_provider_effects
            if effect.stable_key in keys or effect.reference in references
        )
    return replace(
        relay,
        provider_effects=tuple(sorted(effects, key=_effect_order)),
        acked_ops=tuple(acked_ops),
    )


def _begin_read_only_snapshot(session: Session) -> None:
    # Must be the first statement of the transaction.
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


def _in_scope(
    column: InstrumentedAttribute[uuid.UUID], scope: tuple[uuid.UUID, ...] | None
) -> ColumnElement[bool]:
    return column.in_(scope) if scope is not None else true()


def _opt(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _effect_order(effect: ProviderEffectSnap) -> tuple[str, str, str]:
    return (effect.provider_account_id, effect.stable_key, effect.status)


def _read_relaypay(session: Session, scope: tuple[uuid.UUID, ...] | None) -> SystemSnapshot:
    def org(column: InstrumentedAttribute[uuid.UUID]) -> ColumnElement[bool]:
        return _in_scope(column, scope)

    accounts = tuple(
        LedgerAccountSnap(
            str(row.id),
            row.code,
            row.account_type,
            row.currency,
            str(row.organisation_id),
            str(row.environment_id),
            _opt(row.merchant_account_id),
        )
        for row in session.execute(
            select(
                LedgerAccount.id,
                LedgerAccount.code,
                LedgerAccount.account_type,
                LedgerAccount.currency,
                LedgerAccount.organisation_id,
                LedgerAccount.environment_id,
                LedgerAccount.merchant_account_id,
            )
            .where(org(LedgerAccount.organisation_id))
            .order_by(LedgerAccount.id)
        )
    )
    journals = tuple(
        JournalSnap(
            str(row.id),
            row.public_id,
            row.journal_type,
            row.reference_type,
            str(row.reference_id),
            row.currency,
            str(row.organisation_id),
            str(row.environment_id),
            _opt(row.merchant_account_id),
            _opt(row.provider_operation_id),
        )
        for row in session.execute(
            select(
                Journal.id,
                Journal.public_id,
                Journal.journal_type,
                Journal.reference_type,
                Journal.reference_id,
                Journal.currency,
                Journal.organisation_id,
                Journal.environment_id,
                Journal.merchant_account_id,
                Journal.provider_operation_id,
            )
            .where(org(Journal.organisation_id))
            .order_by(Journal.id)
        )
    )
    postings = tuple(_stream_postings(session, scope))

    merchants = session.scalars(
        select(MerchantAccount)
        .where(org(MerchantAccount.organisation_id))
        .order_by(MerchantAccount.id)
    ).all()
    balances: list[MerchantBalanceSnap] = []
    merchants_with_ledger = set(
        session.scalars(
            select(LedgerAccount.merchant_account_id)
            .where(
                org(LedgerAccount.organisation_id), LedgerAccount.merchant_account_id.is_not(None)
            )
            .distinct()
        )
    )
    for merchant in merchants:
        if merchant.id not in merchants_with_ledger:
            # A merchant that never had ledger accounts cannot have postings: all zeros.
            balances.append(
                MerchantBalanceSnap(
                    str(merchant.id),
                    str(merchant.organisation_id),
                    str(merchant.environment_id),
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            )
            continue
        derived = derive_balances(session, merchant)
        balances.append(
            MerchantBalanceSnap(
                str(merchant.id),
                str(merchant.organisation_id),
                str(merchant.environment_id),
                derived.pending,
                derived.available,
                derived.receivable,
                derived.reserved,
                derived.payout_eligible,
            )
        )

    payouts = tuple(
        PayoutSnap(
            str(row.id),
            row.public_id,
            str(row.merchant_account_id),
            row.amount,
            row.status,
            _opt(row.journal_id),
        )
        for row in session.execute(
            select(
                Payout.id,
                Payout.public_id,
                Payout.merchant_account_id,
                Payout.amount,
                Payout.status,
                Payout.journal_id,
            )
            .where(org(Payout.organisation_id))
            .order_by(Payout.id)
        )
    )
    reservations = tuple(
        PayoutReservationSnap(
            str(row.payout_id),
            str(row.merchant_account_id),
            row.sequence,
            row.action,
            row.amount_delta,
        )
        for row in session.execute(
            select(
                PayoutReservationHistory.payout_id,
                PayoutReservationHistory.merchant_account_id,
                PayoutReservationHistory.sequence,
                PayoutReservationHistory.action,
                PayoutReservationHistory.amount_delta,
            )
            .where(org(PayoutReservationHistory.organisation_id))
            .order_by(PayoutReservationHistory.payout_id, PayoutReservationHistory.sequence)
        )
    )
    settlement_items = tuple(
        SettlementItemSnap(str(row.id), str(row.capture_id), str(row.journal_id), row.amount)
        for row in session.execute(
            select(
                SettlementItem.id,
                SettlementItem.capture_id,
                SettlementItem.journal_id,
                SettlementItem.amount,
            )
            .where(org(SettlementItem.organisation_id))
            .order_by(SettlementItem.id)
        )
    )

    # `status` is added to payment_intents by M16; read it when the column exists.
    status_column = getattr(PaymentIntent, "status", None)
    payment_rows = session.execute(
        select(
            PaymentIntent.id,
            PaymentIntent.public_id,
            PaymentIntent.organisation_id,
            PaymentIntent.environment_id,
            PaymentIntent.merchant_account_id,
            PaymentIntent.amount,
            PaymentIntent.currency,
            *([status_column] if status_column is not None else []),
        )
        .where(org(PaymentIntent.organisation_id))
        .order_by(PaymentIntent.id)
    ).all()
    payment_intents = tuple(
        PaymentIntentSnap(
            str(row[0]),
            row[1],
            str(row[2]),
            str(row[3]),
            str(row[4]),
            row[5],
            row[6],
            row[7] if status_column is not None else None,
        )
        for row in payment_rows
    )
    authorizations = tuple(
        AuthorizationSnap(
            str(row.id),
            row.public_id,
            str(row.payment_intent_id),
            str(row.provider_operation_id),
            row.amount,
            row.status,
        )
        for row in session.execute(
            select(
                Authorization.id,
                Authorization.public_id,
                Authorization.payment_intent_id,
                Authorization.provider_operation_id,
                Authorization.amount,
                Authorization.status,
            )
            .where(org(Authorization.organisation_id))
            .order_by(Authorization.id)
        )
    )
    captures = tuple(
        CaptureSnap(
            str(row.id),
            row.public_id,
            str(row.payment_intent_id),
            str(row.authorization_id),
            str(row.provider_operation_id),
            row.amount,
            row.status,
            _opt(row.journal_id),
        )
        for row in session.execute(
            select(
                Capture.id,
                Capture.public_id,
                Capture.payment_intent_id,
                Capture.authorization_id,
                Capture.provider_operation_id,
                Capture.amount,
                Capture.status,
                Capture.journal_id,
            )
            .where(org(Capture.organisation_id))
            .order_by(Capture.id)
        )
    )
    refunds = tuple(
        RefundSnap(
            str(row.id),
            row.public_id,
            str(row.payment_intent_id),
            str(row.capture_id),
            str(row.provider_operation_id),
            row.amount,
            row.status,
            _opt(row.journal_id),
        )
        for row in session.execute(
            select(
                Refund.id,
                Refund.public_id,
                Refund.payment_intent_id,
                Refund.capture_id,
                Refund.provider_operation_id,
                Refund.amount,
                Refund.status,
                Refund.journal_id,
            )
            .where(org(Refund.organisation_id))
            .order_by(Refund.id)
        )
    )
    operations = tuple(
        ProviderOperationSnap(
            str(row.id),
            row.public_id,
            str(row.payment_intent_id),
            row.resource_type,
            str(row.resource_id),
            row.kind,
            row.stable_provider_key,
            row.status,
            row.review_reason,
            None if row.terminal_response_sha256 is None else bytes(row.terminal_response_sha256),
        )
        for row in session.execute(
            select(
                ProviderOperation.id,
                ProviderOperation.public_id,
                ProviderOperation.payment_intent_id,
                ProviderOperation.resource_type,
                ProviderOperation.resource_id,
                ProviderOperation.kind,
                ProviderOperation.stable_provider_key,
                ProviderOperation.status,
                ProviderOperation.review_reason,
                ProviderOperation.terminal_response_sha256,
            )
            .where(org(ProviderOperation.organisation_id))
            .order_by(ProviderOperation.id)
        )
    )
    history = _number_history(
        session.execute(
            select(
                OperationHistory.provider_operation_id,
                OperationHistory.from_status,
                OperationHistory.to_status,
                OperationHistory.reason_code,
            )
            .where(org(OperationHistory.organisation_id))
            .order_by(
                OperationHistory.provider_operation_id,
                OperationHistory.created_at,
                OperationHistory.id,
            )
        ).all()
    )
    idempotency = tuple(
        IdempotencyRecordSnap(
            str(row.id),
            str(row.provider_operation_id),
            row.key_hint,
            row.is_terminal,
            None if row.response_sha256 is None else bytes(row.response_sha256),
        )
        for row in session.execute(
            select(
                IdempotencyRecord.id,
                IdempotencyRecord.provider_operation_id,
                IdempotencyRecord.key_hint,
                IdempotencyRecord.is_terminal,
                IdempotencyRecord.response_sha256,
            )
            .where(
                org(IdempotencyRecord.organisation_id),
                IdempotencyRecord.provider_operation_id.is_not(None),
            )
            .order_by(IdempotencyRecord.id)
        )
    )
    balance_transactions = tuple(
        BalanceTransactionSnap(
            str(row.id),
            str(row.journal_id),
            str(row.merchant_account_id),
            row.transaction_type,
            row.pending_delta,
            row.available_delta,
            row.receivable_delta,
            row.payout_clearing_delta,
        )
        for row in session.execute(
            select(
                BalanceTransaction.id,
                BalanceTransaction.journal_id,
                BalanceTransaction.merchant_account_id,
                BalanceTransaction.transaction_type,
                BalanceTransaction.pending_delta,
                BalanceTransaction.available_delta,
                BalanceTransaction.receivable_delta,
                BalanceTransaction.payout_clearing_delta,
            )
            .where(org(BalanceTransaction.organisation_id))
            .order_by(BalanceTransaction.id)
        )
    )
    return SystemSnapshot(
        ledger_accounts=accounts,
        journals=journals,
        postings=postings,
        merchant_balances=tuple(balances),
        payouts=payouts,
        payout_reservations=reservations,
        settlement_items=settlement_items,
        payment_intents=payment_intents,
        authorizations=authorizations,
        captures=captures,
        refunds=refunds,
        provider_operations=operations,
        operation_history=history,
        idempotency_records=idempotency,
        balance_transactions=balance_transactions,
    )


def _stream_postings(
    session: Session, scope: tuple[uuid.UUID, ...] | None
) -> Iterable[PostingSnap]:
    result = session.execute(
        select(Posting.journal_id, Posting.account_id, Posting.side, Posting.amount)
        .where(_in_scope(Posting.organisation_id, scope))
        .order_by(Posting.journal_id, Posting.id)
        .execution_options(yield_per=POSTING_BATCH_SIZE)
    )
    for row in result:
        yield PostingSnap(str(row.journal_id), str(row.account_id), row.side, row.amount)


def _number_history(
    rows: Sequence[Row[tuple[uuid.UUID, str | None, str, str]]],
) -> tuple[OperationHistorySnap, ...]:
    snaps: list[OperationHistorySnap] = []
    previous: uuid.UUID | None = None
    ordinal = 0
    for row in rows:
        operation_id, from_status, to_status, reason_code = row
        ordinal = ordinal + 1 if operation_id == previous else 0
        previous = operation_id
        snaps.append(
            OperationHistorySnap(str(operation_id), ordinal, from_status, to_status, reason_code)
        )
    return tuple(snaps)


def _read_provider_effects(
    session: Session, relay: SystemSnapshot, scoped: bool
) -> list[ProviderEffectSnap]:
    statement = select(
        ProviderAccount.public_id,
        ProviderEffect.stable_key,
        ProviderEffect.operation_kind,
        ProviderEffect.amount,
        ProviderEffect.outcome,
        ProviderEffect.reference,
    ).join(ProviderAccount, ProviderAccount.id == ProviderEffect.provider_account_id)
    if scoped:
        keys, references = _scope_keys(relay)
        if not keys and not references:
            return []
        statement = statement.where(
            ProviderEffect.stable_key.in_(keys) | ProviderEffect.reference.in_(references)
        )
    statement = statement.order_by(
        ProviderAccount.public_id, ProviderEffect.stable_key, ProviderEffect.id
    )
    return [
        ProviderEffectSnap(
            stable_key=row.stable_key,
            kind=row.operation_kind,
            amount=row.amount,
            status=row.outcome,
            reference=row.reference,
            provider_account_id=row.public_id,
        )
        for row in session.execute(statement)
    ]


def _scope_keys(relay: SystemSnapshot) -> tuple[set[str], set[str]]:
    """Stable keys and resource public ids of the scoped operations (to scope provider effects)."""
    keys = {operation.stable_provider_key for operation in relay.provider_operations}
    references = (
        {authorization.public_id for authorization in relay.authorizations}
        | {capture.public_id for capture in relay.captures}
        | {refund.public_id for refund in relay.refunds}
    )
    return keys, references
