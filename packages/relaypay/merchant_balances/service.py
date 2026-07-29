import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import Fingerprint, canonical_json_bytes, digest_secret
from relaypay.identity.models import Environment
from relaypay.identity.security import Principal
from relaypay.identity.service import append_audit, require_organisation_admin
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.ledger.service import account, add_balance_transaction, post_journal
from relaypay.merchant_balances.models import (
    BalanceTransaction,
    MerchantAccount,
    SettlementItem,
    SettlementRun,
)
from relaypay.payments.models import Capture, PaymentIntent, Refund

ACCOUNT_TEMPLATES = (
    ("PENDING_PAYABLE_LIABILITY", "Pending merchant payable", "LIABILITY"),
    ("AVAILABLE_PAYABLE_LIABILITY", "Available merchant payable", "LIABILITY"),
    ("PAYOUT_CLEARING_ASSET", "Payout clearing", "ASSET"),
    ("MERCHANT_RECEIVABLE_ASSET", "Merchant receivable", "ASSET"),
)


@dataclass(frozen=True, slots=True)
class MerchantBalances:
    pending: int
    available: int
    receivable: int
    reserved: int
    payout_eligible: int


@dataclass(frozen=True, slots=True)
class SettlementResult:
    status_code: int
    body: bytes
    replayed: bool


def _environment(session: Session, principal: Principal, public_id: str) -> Environment:
    item = session.scalar(
        select(Environment).where(
            Environment.organisation_id == principal.organisation_id,
            Environment.public_id == public_id,
            Environment.status == "ACTIVE",
        )
    )
    if item is None:
        raise not_found("Environment")
    return item


def create_merchant_account(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    reference: str,
    name: str,
    is_default: bool,
) -> MerchantAccount:
    if is_default:
        existing_default = session.scalar(
            select(MerchantAccount)
            .where(
                MerchantAccount.organisation_id == organisation_id,
                MerchantAccount.environment_id == environment_id,
                MerchantAccount.is_default.is_(True),
                MerchantAccount.status == "ACTIVE",
            )
            .with_for_update()
        )
        if existing_default is not None:
            raise RelayPayError(
                code="DEFAULT_MERCHANT_ACCOUNT_EXISTS",
                message="The environment already has an active default merchant account",
                http_status=409,
            )
    merchant = MerchantAccount(
        id=new_uuid(),
        public_id=new_public_id("mac"),
        organisation_id=organisation_id,
        environment_id=environment_id,
        reference=reference,
        name=name,
        currency="INR",
        is_default=is_default,
        status="ACTIVE",
    )
    session.add(merchant)
    session.flush()
    session.add_all(
        [
            LedgerAccount(
                organisation_id=organisation_id,
                environment_id=environment_id,
                merchant_account_id=merchant.id,
                code=code,
                name=account_name,
                account_type=account_type,
                currency="INR",
            )
            for code, account_name, account_type in ACCOUNT_TEMPLATES
        ]
    )
    return merchant


def ensure_default_merchant_account(
    session: Session, *, organisation_id: uuid.UUID, environment_id: uuid.UUID
) -> MerchantAccount:
    merchant = session.scalar(
        select(MerchantAccount).where(
            MerchantAccount.organisation_id == organisation_id,
            MerchantAccount.environment_id == environment_id,
            MerchantAccount.is_default.is_(True),
            MerchantAccount.status == "ACTIVE",
        )
    )
    if merchant is not None:
        return merchant
    return create_merchant_account(
        session,
        organisation_id=organisation_id,
        environment_id=environment_id,
        reference="default",
        name="Default",
        is_default=True,
    )


def create_admin_merchant_account(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    reference: str,
    name: str,
) -> MerchantAccount:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    ensure_default_merchant_account(
        session, organisation_id=principal.organisation_id, environment_id=environment.id
    )
    merchant = create_merchant_account(
        session,
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        reference=reference,
        name=name,
        is_default=False,
    )
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="MERCHANT_ACCOUNT_CREATED",
        target_type="MERCHANT_ACCOUNT",
        target_id=merchant.public_id,
        details={"reference": reference},
    )
    return merchant


def default_merchant_account_id(
    session: Session, *, organisation_id: uuid.UUID, environment_id: uuid.UUID
) -> uuid.UUID:
    return ensure_default_merchant_account(
        session, organisation_id=organisation_id, environment_id=environment_id
    ).id


def _scoped_merchant(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    merchant_public_id: str,
    lock: bool = False,
) -> MerchantAccount:
    statement = select(MerchantAccount).where(
        MerchantAccount.organisation_id == organisation_id,
        MerchantAccount.environment_id == environment_id,
        MerchantAccount.public_id == merchant_public_id,
        MerchantAccount.status == "ACTIVE",
    )
    merchant = session.scalar(statement.with_for_update() if lock else statement)
    if merchant is None:
        raise not_found("Merchant account")
    return merchant


def _account_balance(session: Session, ledger_account: LedgerAccount) -> int:
    value = session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (Posting.side == "CREDIT", Posting.amount),
                        else_=-Posting.amount,
                    )
                ),
                0,
            )
        ).where(Posting.account_id == ledger_account.id)
    )
    credit_normal = int(value or 0)
    return credit_normal if ledger_account.account_type == "LIABILITY" else -credit_normal


def derive_balances(session: Session, merchant: MerchantAccount) -> MerchantBalances:
    pending = _account_balance(
        session,
        account(
            session,
            merchant.organisation_id,
            merchant.environment_id,
            "PENDING_PAYABLE_LIABILITY",
            merchant_account_id=merchant.id,
        ),
    )
    available = _account_balance(
        session,
        account(
            session,
            merchant.organisation_id,
            merchant.environment_id,
            "AVAILABLE_PAYABLE_LIABILITY",
            merchant_account_id=merchant.id,
        ),
    )
    receivable = _account_balance(
        session,
        account(
            session,
            merchant.organisation_id,
            merchant.environment_id,
            "MERCHANT_RECEIVABLE_ASSET",
            merchant_account_id=merchant.id,
        ),
    )
    reserved = 0
    return MerchantBalances(pending, available, receivable, reserved, max(available - reserved, 0))


def list_admin_merchant_accounts(
    session: Session, *, principal: Principal, environment_public_id: str
) -> list[MerchantAccount]:
    environment = _environment(session, principal, environment_public_id)
    ensure_default_merchant_account(
        session, organisation_id=principal.organisation_id, environment_id=environment.id
    )
    return list(
        session.scalars(
            select(MerchantAccount)
            .where(
                MerchantAccount.organisation_id == principal.organisation_id,
                MerchantAccount.environment_id == environment.id,
            )
            .order_by(MerchantAccount.is_default.desc(), MerchantAccount.created_at)
        )
    )


def read_admin_balances(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    merchant_public_id: str,
) -> tuple[MerchantAccount, MerchantBalances]:
    environment = _environment(session, principal, environment_public_id)
    merchant = _scoped_merchant(
        session,
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        merchant_public_id=merchant_public_id,
    )
    return merchant, derive_balances(session, merchant)


def list_balance_transactions(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    merchant_public_id: str,
) -> list[BalanceTransaction]:
    environment = _environment(session, principal, environment_public_id)
    merchant = _scoped_merchant(
        session,
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        merchant_public_id=merchant_public_id,
    )
    return list(
        session.scalars(
            select(BalanceTransaction)
            .where(BalanceTransaction.merchant_account_id == merchant.id)
            .order_by(BalanceTransaction.created_at, BalanceTransaction.id)
        )
    )


def _pending_refunded_for_capture(session: Session, capture: Capture) -> int:
    value = session.scalar(
        select(func.coalesce(func.sum(-BalanceTransaction.pending_delta), 0))
        .join(Journal, Journal.id == BalanceTransaction.journal_id)
        .join(Refund, Refund.id == Journal.reference_id)
        .where(
            BalanceTransaction.transaction_type == "REFUND",
            Refund.capture_id == capture.id,
        )
    )
    return int(value or 0)


def _run_settlement_transaction(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    merchant_public_id: str,
    key_digest: bytes,
    fingerprint: Fingerprint,
) -> SettlementResult:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    existing = session.scalar(
        select(SettlementRun).where(
            SettlementRun.organisation_id == principal.organisation_id,
            SettlementRun.environment_id == environment.id,
            SettlementRun.idempotency_key_digest == key_digest,
        )
    )
    if existing is not None:
        if existing.fingerprint_sha256 != fingerprint.sha256:
            raise RelayPayError(
                code="IDEMPOTENCY_KEY_REUSED",
                message="The idempotency key was already used for a different request",
                http_status=409,
            )
        if existing.response_bytes is None:
            raise RelayPayError(
                code="SETTLEMENT_IN_PROGRESS",
                message="The settlement is still processing",
                http_status=409,
            )
        return SettlementResult(200, existing.response_bytes, True)

    merchant = _scoped_merchant(
        session,
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        merchant_public_id=merchant_public_id,
        lock=True,
    )
    run = SettlementRun(
        id=new_uuid(),
        public_id=new_public_id("stl"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        merchant_account_id=merchant.id,
        idempotency_key_digest=key_digest,
        fingerprint_sha256=fingerprint.sha256,
        status="PROCESSING",
        settled_amount=0,
    )
    session.add(run)
    session.flush()
    captures = list(
        session.scalars(
            select(Capture)
            .join(PaymentIntent, PaymentIntent.id == Capture.payment_intent_id)
            .outerjoin(SettlementItem, SettlementItem.capture_id == Capture.id)
            .where(
                Capture.organisation_id == principal.organisation_id,
                Capture.environment_id == environment.id,
                Capture.status == "SUCCEEDED",
                PaymentIntent.merchant_account_id == merchant.id,
                SettlementItem.id.is_(None),
            )
            .order_by(Capture.captured_at, Capture.id)
            .with_for_update(skip_locked=True, of=Capture)
        )
    )
    item_bodies: list[dict[str, object]] = []
    for capture in captures:
        amount = capture.amount - _pending_refunded_for_capture(session, capture)
        if amount <= 0:
            continue
        receivable = max(derive_balances(session, merchant).receivable, 0)
        receivable_offset = min(receivable, amount)
        available_credit = amount - receivable_offset
        entries: list[tuple[LedgerAccount, Literal["DEBIT", "CREDIT"], int]] = [
            (
                account(
                    session,
                    principal.organisation_id,
                    environment.id,
                    "PENDING_PAYABLE_LIABILITY",
                    merchant_account_id=merchant.id,
                ),
                "DEBIT",
                amount,
            )
        ]
        if receivable_offset:
            entries.append(
                (
                    account(
                        session,
                        principal.organisation_id,
                        environment.id,
                        "MERCHANT_RECEIVABLE_ASSET",
                        merchant_account_id=merchant.id,
                    ),
                    "CREDIT",
                    receivable_offset,
                )
            )
        if available_credit:
            entries.append(
                (
                    account(
                        session,
                        principal.organisation_id,
                        environment.id,
                        "AVAILABLE_PAYABLE_LIABILITY",
                        merchant_account_id=merchant.id,
                    ),
                    "CREDIT",
                    available_credit,
                )
            )
        journal = post_journal(
            session,
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            merchant_account_id=merchant.id,
            provider_operation_id=None,
            journal_type="SETTLEMENT",
            reference_type="CAPTURE",
            reference_id=capture.id,
            entries=entries,
        )
        item = SettlementItem(
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            settlement_run_id=run.id,
            capture_id=capture.id,
            journal_id=journal.journal_id,
            amount=amount,
            currency="INR",
        )
        session.add(item)
        add_balance_transaction(
            session,
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            merchant_account_id=merchant.id,
            journal_id=journal.journal_id,
            transaction_type="SETTLEMENT",
            pending_delta=-amount,
            available_delta=available_credit,
            receivable_delta=-receivable_offset,
        )
        run.settled_amount += amount
        item_bodies.append({"captureId": capture.public_id, "amount": amount})

    body = canonical_json_bytes(
        {
            "id": run.public_id,
            "merchantAccountId": merchant.public_id,
            "status": "COMPLETED",
            "settledAmount": run.settled_amount,
            "currency": "INR",
            "items": item_bodies,
        }
    )
    run.status = "COMPLETED"
    run.response_bytes = body
    run.response_sha256 = hashlib.sha256(body).digest()
    run.completed_at = datetime.now(UTC)
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="SETTLEMENT_COMPLETED",
        target_type="SETTLEMENT_RUN",
        target_id=run.public_id,
        details={"merchantAccountId": merchant.public_id, "settledAmount": run.settled_amount},
    )
    return SettlementResult(201, body, False)


def run_settlement(
    factory: sessionmaker[Session],
    *,
    principal: Principal,
    environment_public_id: str,
    merchant_public_id: str,
    idempotency_key: str,
    fingerprint: Fingerprint,
    key_pepper: str,
) -> SettlementResult:
    key_digest = digest_secret(idempotency_key, key_pepper)
    try:
        with factory() as session, session.begin():
            return _run_settlement_transaction(
                session,
                principal=principal,
                environment_public_id=environment_public_id,
                merchant_public_id=merchant_public_id,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )
    except IntegrityError:
        with factory() as session, session.begin():
            return _run_settlement_transaction(
                session,
                principal=principal,
                environment_public_id=environment_public_id,
                merchant_public_id=merchant_public_id,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )
