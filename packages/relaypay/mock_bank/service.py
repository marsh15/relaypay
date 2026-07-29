import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_uuid
from relaypay.mock_bank.models import BankAccount, BankEffect, BankFaultDirective


@dataclass(frozen=True, slots=True)
class BankTransferCommand:
    account_id: str
    stable_key: str
    beneficiary_reference: str
    payout_reference: str
    amount: int
    currency: str

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "accountId": self.account_id,
                "amount": self.amount,
                "beneficiaryReference": self.beneficiary_reference,
                "currency": self.currency,
                "payoutReference": self.payout_reference,
                "stableKey": self.stable_key,
            }
        )


@dataclass(frozen=True, slots=True)
class BankReply:
    status_code: int
    body: bytes
    headers: dict[str, str]


def signature(body: bytes, signing_secret: str) -> str:
    return hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()


def _body(effect: BankEffect, account_id: str) -> bytes:
    return canonical_json_bytes(
        {
            "accountId": account_id,
            "amount": effect.amount,
            "beneficiaryReference": effect.beneficiary_reference,
            "currency": effect.currency,
            "declineCode": effect.decline_code,
            "effectId": str(effect.id),
            "outcome": effect.outcome,
            "payoutReference": effect.payout_reference,
            "stableKey": effect.stable_key,
        }
    )


def _reply(body: bytes, signing_secret: str) -> BankReply:
    return BankReply(
        200,
        body,
        {
            "Content-Type": "application/json",
            "X-Bank-Signature": signature(body, signing_secret),
        },
    )


def apply_transfer(
    factory: sessionmaker[Session],
    *,
    command: BankTransferCommand,
    signing_secret: str,
) -> BankReply:
    request_bytes = command.canonical_bytes()
    request_sha256 = hashlib.sha256(request_bytes).digest()
    selected_fault: str | None = None
    with factory() as session, session.begin():
        account = session.scalar(
            select(BankAccount).where(BankAccount.public_id == command.account_id)
        )
        if account is None:
            raise RelayPayError(
                code="BANK_ACCOUNT_NOT_FOUND",
                message="Synthetic bank account was not found",
                http_status=404,
            )
        existing = session.scalar(
            select(BankEffect).where(
                BankEffect.bank_account_id == account.id,
                BankEffect.stable_key == command.stable_key,
            )
        )
        if existing is not None:
            if existing.request_sha256 != request_sha256:
                raise RelayPayError(
                    code="BANK_KEY_CONFLICT",
                    message="Stable bank key is bound to different request bytes",
                    http_status=409,
                )
            return _reply(
                existing.response_bytes or _body(existing, account.public_id), signing_secret
            )

        directive = session.scalar(
            select(BankFaultDirective)
            .where(
                BankFaultDirective.bank_account_id == account.id,
                BankFaultDirective.stable_key == command.stable_key,
                BankFaultDirective.remaining_uses > 0,
            )
            .order_by(BankFaultDirective.created_at, BankFaultDirective.id)
            .with_for_update()
        )
        if directive is not None:
            selected_fault = directive.fault_type
            directive.remaining_uses -= 1
        outcome = (
            "DECLINED"
            if selected_fault == "DECLINE"
            else "PENDING"
            if selected_fault == "PENDING"
            else "SUCCEEDED"
        )
        effect = BankEffect(
            id=new_uuid(),
            bank_account_id=account.id,
            stable_key=command.stable_key,
            beneficiary_reference=command.beneficiary_reference,
            payout_reference=command.payout_reference,
            amount=command.amount,
            currency=command.currency,
            request_sha256=request_sha256,
            outcome=outcome,
            decline_code="BENEFICIARY_REJECTED" if outcome == "DECLINED" else None,
            completed_at=None if outcome == "PENDING" else datetime.now(UTC),
        )
        effect.response_bytes = None if outcome == "PENDING" else _body(effect, account.public_id)
        session.add(effect)
        session.flush()
        body = effect.response_bytes or _body(effect, account.public_id)
    if selected_fault == "LOSE_RESPONSE":
        return BankReply(599, b"", {})
    return _reply(body, signing_secret)


def lookup_transfer(
    factory: sessionmaker[Session],
    *,
    account_public_id: str,
    stable_key: str,
    signing_secret: str,
) -> BankReply:
    with factory() as session, session.begin():
        effect = session.scalar(
            select(BankEffect)
            .join(BankAccount, BankAccount.id == BankEffect.bank_account_id)
            .where(
                BankAccount.public_id == account_public_id,
                BankEffect.stable_key == stable_key,
            )
        )
        if effect is None:
            raise RelayPayError(
                code="BANK_EFFECT_NOT_FOUND",
                message="Synthetic bank effect was not found",
                http_status=404,
            )
        return _reply(effect.response_bytes or _body(effect, account_public_id), signing_secret)


def configure_fault(
    factory: sessionmaker[Session],
    *,
    account_public_id: str,
    stable_key: str,
    fault_type: str,
) -> None:
    with factory() as session, session.begin():
        account = session.scalar(
            select(BankAccount).where(BankAccount.public_id == account_public_id)
        )
        if account is None:
            raise RelayPayError(
                code="BANK_ACCOUNT_NOT_FOUND",
                message="Synthetic bank account was not found",
                http_status=404,
            )
        directive = session.scalar(
            select(BankFaultDirective).where(
                BankFaultDirective.bank_account_id == account.id,
                BankFaultDirective.stable_key == stable_key,
                BankFaultDirective.fault_type == fault_type,
            )
        )
        if directive is None:
            session.add(
                BankFaultDirective(
                    bank_account_id=account.id,
                    stable_key=stable_key,
                    fault_type=fault_type,
                    remaining_uses=1,
                )
            )
        else:
            directive.remaining_uses += 1


def parse_command(request_bytes: bytes) -> BankTransferCommand:
    data = json.loads(request_bytes)
    return BankTransferCommand(
        account_id=data["accountId"],
        stable_key=data["stableKey"],
        beneficiary_reference=data["beneficiaryReference"],
        payout_reference=data["payoutReference"],
        amount=data["amount"],
        currency=data["currency"],
    )
