import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError
from relaypay.identity.environments import resolve_environment_id
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import Journal, LedgerAccount, Posting

JournalType = Literal["CAPTURE", "REFUND"]


@dataclass(frozen=True, slots=True)
class JournalResult:
    journal_id: uuid.UUID
    public_id: str
    debit_total: int
    credit_total: int


def _account(
    session: Session, organisation_id: uuid.UUID, environment_id: uuid.UUID, code: str
) -> LedgerAccount:
    account = session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.organisation_id == organisation_id,
            LedgerAccount.environment_id == environment_id,
            LedgerAccount.code == code,
            LedgerAccount.currency == "INR",
        )
    )
    if account is None:
        raise RelayPayError(
            code="LEDGER_ACCOUNT_MISSING",
            message="Required INR ledger account is missing",
            http_status=500,
            details={"account_code": code},
        )
    return account


def _post(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    provider_operation_id: uuid.UUID,
    journal_type: JournalType,
    reference_id: uuid.UUID,
    amount: int,
    debit_code: str,
    credit_code: str,
) -> JournalResult:
    if amount <= 0:
        raise ValueError("journal amount must be positive paise")

    debit = _account(session, organisation_id, environment_id, debit_code)
    credit = _account(session, organisation_id, environment_id, credit_code)
    journal = Journal(
        id=new_uuid(),
        public_id=new_public_id("jrn"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        provider_operation_id=provider_operation_id,
        journal_type=journal_type,
        reference_type=journal_type,
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
                account_id=debit.id,
                side="DEBIT",
                amount=amount,
                currency="INR",
            ),
            Posting(
                organisation_id=organisation_id,
                environment_id=environment_id,
                journal_id=journal.id,
                account_id=credit.id,
                side="CREDIT",
                amount=amount,
                currency="INR",
            ),
        ]
    )
    return JournalResult(journal.id, journal.public_id, amount, amount)


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
    return _post(
        session,
        organisation_id=organisation_id,
        environment_id=resolved_environment_id,
        provider_operation_id=provider_operation_id,
        journal_type="CAPTURE",
        reference_id=capture_id,
        amount=amount,
        debit_code="PROVIDER_CLEARING_ASSET",
        credit_code="MERCHANT_PAYABLE_LIABILITY",
    )


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
    return _post(
        session,
        organisation_id=organisation_id,
        environment_id=resolved_environment_id,
        provider_operation_id=provider_operation_id,
        journal_type="REFUND",
        reference_id=refund_id,
        amount=amount,
        debit_code="MERCHANT_PAYABLE_LIABILITY",
        credit_code="PROVIDER_CLEARING_ASSET",
    )
