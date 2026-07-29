import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError
from relaypay.identity.environments import resolve_environment_id
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.merchant_balances.models import BalanceTransaction, SettlementItem
from relaypay.payments.models import Capture, PaymentIntent, Refund

JournalType = Literal["CAPTURE", "REFUND", "SETTLEMENT", "PAYOUT"]


@dataclass(frozen=True, slots=True)
class JournalResult:
    journal_id: uuid.UUID
    public_id: str
    debit_total: int
    credit_total: int


def account(
    session: Session,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    code: str,
    *,
    merchant_account_id: uuid.UUID | None = None,
) -> LedgerAccount:
    statement = select(LedgerAccount).where(
        LedgerAccount.organisation_id == organisation_id,
        LedgerAccount.environment_id == environment_id,
        LedgerAccount.code == code,
        LedgerAccount.currency == "INR",
    )
    if merchant_account_id is None:
        statement = statement.where(LedgerAccount.merchant_account_id.is_(None))
    else:
        statement = statement.where(LedgerAccount.merchant_account_id == merchant_account_id)
    item = session.scalar(statement)
    if item is None:
        raise RelayPayError(
            code="LEDGER_ACCOUNT_MISSING",
            message="Required INR ledger account is missing",
            http_status=500,
            details={"account_code": code},
        )
    return item


def post_journal(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
    provider_operation_id: uuid.UUID | None,
    journal_type: JournalType,
    reference_type: str,
    reference_id: uuid.UUID,
    entries: list[tuple[LedgerAccount, Literal["DEBIT", "CREDIT"], int]],
) -> JournalResult:
    debit_total = sum(amount for _, side, amount in entries if side == "DEBIT")
    credit_total = sum(amount for _, side, amount in entries if side == "CREDIT")
    if debit_total <= 0 or debit_total != credit_total:
        raise ValueError("journal entries must be positive and balanced")
    if any(amount <= 0 for _, _, amount in entries):
        raise ValueError("journal entries must be positive")

    journal = Journal(
        id=new_uuid(),
        public_id=new_public_id("jrn"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
        provider_operation_id=provider_operation_id,
        journal_type=journal_type,
        reference_type=reference_type,
        reference_id=reference_id,
        currency="INR",
    )
    session.add(journal)
    session.flush()
    session.add_all(
        [
            Posting(
                organisation_id=organisation_id,
                environment_id=environment_id,
                journal_id=journal.id,
                account_id=ledger_account.id,
                side=side,
                amount=amount,
                currency="INR",
            )
            for ledger_account, side, amount in entries
        ]
    )
    return JournalResult(journal.id, journal.public_id, debit_total, credit_total)


def add_balance_transaction(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_account_id: uuid.UUID,
    journal_id: uuid.UUID,
    transaction_type: JournalType,
    pending_delta: int = 0,
    available_delta: int = 0,
    receivable_delta: int = 0,
    payout_clearing_delta: int = 0,
) -> BalanceTransaction:
    transaction = BalanceTransaction(
        id=new_uuid(),
        public_id=new_public_id("btx"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        merchant_account_id=merchant_account_id,
        journal_id=journal_id,
        transaction_type=transaction_type,
        pending_delta=pending_delta,
        available_delta=available_delta,
        receivable_delta=receivable_delta,
        payout_clearing_delta=payout_clearing_delta,
        currency="INR",
    )
    session.add(transaction)
    return transaction


def post_capture_journal(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    provider_operation_id: uuid.UUID,
    capture_id: uuid.UUID,
    amount: int,
) -> JournalResult:
    resolved_environment_id = resolve_environment_id(
        session, organisation_id=organisation_id, environment_id=environment_id
    )
    merchant_account_id = session.scalar(
        select(PaymentIntent.merchant_account_id)
        .join(Capture, Capture.payment_intent_id == PaymentIntent.id)
        .where(Capture.id == capture_id)
    )
    if merchant_account_id is None:
        raise RuntimeError("capture payment merchant account is missing")
    result = post_journal(
        session,
        organisation_id=organisation_id,
        environment_id=resolved_environment_id,
        merchant_account_id=merchant_account_id,
        provider_operation_id=provider_operation_id,
        journal_type="CAPTURE",
        reference_type="CAPTURE",
        reference_id=capture_id,
        entries=[
            (
                account(
                    session,
                    organisation_id,
                    resolved_environment_id,
                    "PROVIDER_CLEARING_ASSET",
                ),
                "DEBIT",
                amount,
            ),
            (
                account(
                    session,
                    organisation_id,
                    resolved_environment_id,
                    "PENDING_PAYABLE_LIABILITY",
                    merchant_account_id=merchant_account_id,
                ),
                "CREDIT",
                amount,
            ),
        ],
    )
    add_balance_transaction(
        session,
        organisation_id=organisation_id,
        environment_id=resolved_environment_id,
        merchant_account_id=merchant_account_id,
        journal_id=result.journal_id,
        transaction_type="CAPTURE",
        pending_delta=amount,
    )
    return result


def _refund_sources(
    session: Session,
    *,
    refund: Refund,
    merchant_account_id: uuid.UUID,
) -> tuple[int, int, int]:
    settled = session.scalar(
        select(func.coalesce(func.sum(SettlementItem.amount), 0)).where(
            SettlementItem.capture_id == refund.capture_id
        )
    )
    prior_pending_refunds = session.scalar(
        select(func.coalesce(func.sum(-BalanceTransaction.pending_delta), 0))
        .join(Journal, Journal.id == BalanceTransaction.journal_id)
        .join(Refund, Refund.id == Journal.reference_id)
        .where(
            BalanceTransaction.merchant_account_id == merchant_account_id,
            BalanceTransaction.transaction_type == "REFUND",
            Refund.payment_intent_id == refund.payment_intent_id,
        )
    )
    capture_amount = session.scalar(select(Capture.amount).where(Capture.id == refund.capture_id))
    if capture_amount is None:
        raise RuntimeError("refund capture is missing")
    pending = max(int(capture_amount) - int(settled or 0) - int(prior_pending_refunds or 0), 0)
    available_account = account(
        session,
        refund.organisation_id,
        refund.environment_id,
        "AVAILABLE_PAYABLE_LIABILITY",
        merchant_account_id=merchant_account_id,
    )
    available = session.scalar(
        select(
            func.coalesce(
                func.sum(case((Posting.side == "CREDIT", Posting.amount), else_=-Posting.amount)),
                0,
            )
        ).where(Posting.account_id == available_account.id)
    )
    pending_use = min(refund.amount, pending)
    available_use = min(refund.amount - pending_use, max(int(available or 0), 0))
    return pending_use, available_use, refund.amount - pending_use - available_use


def post_refund_journal(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    provider_operation_id: uuid.UUID,
    refund_id: uuid.UUID,
    amount: int,
) -> JournalResult:
    resolved_environment_id = resolve_environment_id(
        session, organisation_id=organisation_id, environment_id=environment_id
    )
    refund = session.scalar(select(Refund).where(Refund.id == refund_id))
    if refund is None or refund.amount != amount:
        raise RuntimeError("refund accounting resource is missing or inconsistent")
    merchant_account_id = session.scalar(
        select(PaymentIntent.merchant_account_id).where(
            PaymentIntent.id == refund.payment_intent_id
        )
    )
    if merchant_account_id is None:
        raise RuntimeError("refund payment merchant account is missing")
    pending_use, available_use, receivable_use = _refund_sources(
        session, refund=refund, merchant_account_id=merchant_account_id
    )
    entries: list[tuple[LedgerAccount, Literal["DEBIT", "CREDIT"], int]] = []
    if pending_use:
        entries.append(
            (
                account(
                    session,
                    organisation_id,
                    resolved_environment_id,
                    "PENDING_PAYABLE_LIABILITY",
                    merchant_account_id=merchant_account_id,
                ),
                "DEBIT",
                pending_use,
            )
        )
    if available_use:
        entries.append(
            (
                account(
                    session,
                    organisation_id,
                    resolved_environment_id,
                    "AVAILABLE_PAYABLE_LIABILITY",
                    merchant_account_id=merchant_account_id,
                ),
                "DEBIT",
                available_use,
            )
        )
    if receivable_use:
        entries.append(
            (
                account(
                    session,
                    organisation_id,
                    resolved_environment_id,
                    "MERCHANT_RECEIVABLE_ASSET",
                    merchant_account_id=merchant_account_id,
                ),
                "DEBIT",
                receivable_use,
            )
        )
    entries.append(
        (
            account(
                session,
                organisation_id,
                resolved_environment_id,
                "PROVIDER_CLEARING_ASSET",
            ),
            "CREDIT",
            amount,
        )
    )
    result = post_journal(
        session,
        organisation_id=organisation_id,
        environment_id=resolved_environment_id,
        merchant_account_id=merchant_account_id,
        provider_operation_id=provider_operation_id,
        journal_type="REFUND",
        reference_type="REFUND",
        reference_id=refund_id,
        entries=entries,
    )
    add_balance_transaction(
        session,
        organisation_id=organisation_id,
        environment_id=resolved_environment_id,
        merchant_account_id=merchant_account_id,
        journal_id=result.journal_id,
        transaction_type="REFUND",
        pending_delta=-pending_use,
        available_delta=-available_use,
        receivable_delta=receivable_use,
    )
    return result
