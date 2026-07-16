import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_uuid
from relaypay.mock_provider.models import (
    ProviderAccount,
    ProviderEffect,
    ProviderFaultDirective,
)


@dataclass(frozen=True, slots=True)
class EffectCommand:
    account_id: str
    stable_key: str
    operation_kind: str
    reference: str
    amount: int
    currency: str

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "accountId": self.account_id,
                "amount": self.amount,
                "currency": self.currency,
                "operationKind": self.operation_kind,
                "reference": self.reference,
                "stableKey": self.stable_key,
            }
        )


@dataclass(frozen=True, slots=True)
class ProviderReply:
    status_code: int
    body: bytes
    headers: dict[str, str]


def signature(body: bytes, signing_secret: str) -> str:
    return hmac.new(signing_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _reply(body: bytes, signing_secret: str, *, status_code: int = 200) -> ProviderReply:
    return ProviderReply(
        status_code,
        body,
        {
            "Content-Type": "application/json",
            "X-Provider-Signature": signature(body, signing_secret),
        },
    )


def _effect_body(effect: ProviderEffect, account_public_id: str) -> bytes:
    return canonical_json_bytes(
        {
            "accountId": account_public_id,
            "amount": effect.amount,
            "currency": effect.currency,
            "declineCode": effect.decline_code,
            "effectId": str(effect.id),
            "operationKind": effect.operation_kind,
            "outcome": effect.outcome,
            "reference": effect.reference,
            "stableKey": effect.stable_key,
        }
    )


def apply_effect(
    factory: sessionmaker[Session],
    *,
    command: EffectCommand,
    signing_secret: str,
) -> ProviderReply:
    request_bytes = command.canonical_bytes()
    request_sha = hashlib.sha256(request_bytes).digest()
    selected_fault: str | None = None
    try:
        with factory() as session, session.begin():
            account = session.scalar(
                select(ProviderAccount).where(ProviderAccount.public_id == command.account_id)
            )
            if account is None:
                raise RelayPayError(
                    code="PROVIDER_ACCOUNT_NOT_FOUND",
                    message="Provider account was not found",
                    http_status=404,
                )
            existing = session.scalar(
                select(ProviderEffect).where(
                    ProviderEffect.provider_account_id == account.id,
                    ProviderEffect.stable_key == command.stable_key,
                )
            )
            if existing is not None:
                if existing.request_sha256 != request_sha:
                    raise RelayPayError(
                        code="PROVIDER_KEY_CONFLICT",
                        message="Stable provider key is bound to different request bytes",
                        http_status=409,
                    )
                body = existing.response_bytes or _effect_body(existing, account.public_id)
                return _reply(body, signing_secret)

            directive = session.scalar(
                select(ProviderFaultDirective)
                .where(
                    ProviderFaultDirective.provider_account_id == account.id,
                    ProviderFaultDirective.stable_key == command.stable_key,
                    ProviderFaultDirective.remaining_uses > 0,
                )
                .order_by(ProviderFaultDirective.created_at, ProviderFaultDirective.id)
                .with_for_update()
            )
            if directive is not None:
                selected_fault = directive.fault_type
                directive.remaining_uses -= 1

            outcome = "PENDING" if selected_fault == "PENDING" else "SUCCEEDED"
            decline_code = None
            if selected_fault == "DECLINE":
                outcome = "DECLINED"
                decline_code = "DO_NOT_HONOR"
            effect = ProviderEffect(
                id=new_uuid(),
                provider_account_id=account.id,
                stable_key=command.stable_key,
                operation_kind=command.operation_kind,
                reference=command.reference,
                amount=command.amount,
                currency=command.currency,
                request_sha256=request_sha,
                outcome=outcome,
                decline_code=decline_code,
                completed_at=None if outcome == "PENDING" else datetime.now(UTC),
            )
            correct_body = _effect_body(effect, account.public_id)
            effect.response_bytes = None if outcome == "PENDING" else correct_body
            session.add(effect)
    except RelayPayError:
        raise
    except IntegrityError as error:
        raise RelayPayError(
            code="PROVIDER_EFFECT_CONFLICT",
            message="A concurrent request selected the stable provider effect",
            http_status=409,
        ) from error

    if selected_fault == "LOSE_RESPONSE":
        return ProviderReply(599, b"", {})
    if selected_fault == "MALFORMED":
        malformed = b'{"outcome":'
        return _reply(malformed, signing_secret)
    if selected_fault == "UNSIGNED":
        return ProviderReply(200, correct_body, {"Content-Type": "application/json"})
    if selected_fault == "MISMATCHED":
        mismatched = canonical_json_bytes(
            {
                "accountId": command.account_id,
                "amount": command.amount + 1,
                "currency": command.currency,
                "declineCode": None,
                "effectId": str(effect.id),
                "operationKind": command.operation_kind,
                "outcome": "SUCCEEDED",
                "reference": command.reference,
                "stableKey": command.stable_key,
            }
        )
        return _reply(mismatched, signing_secret)
    return _reply(correct_body, signing_secret)


def lookup_effect(
    factory: sessionmaker[Session],
    *,
    account_public_id: str,
    stable_key: str,
    signing_secret: str,
) -> ProviderReply:
    with factory() as session, session.begin():
        effect = session.scalar(
            select(ProviderEffect)
            .join(ProviderAccount, ProviderAccount.id == ProviderEffect.provider_account_id)
            .where(
                ProviderAccount.public_id == account_public_id,
                ProviderEffect.stable_key == stable_key,
            )
        )
        if effect is None:
            raise RelayPayError(
                code="PROVIDER_EFFECT_NOT_FOUND",
                message="Provider effect was not found",
                http_status=404,
            )
        body = effect.response_bytes or _effect_body(effect, account_public_id)
        return _reply(body, signing_secret)


def configure_fault(
    factory: sessionmaker[Session],
    *,
    account_public_id: str,
    stable_key: str,
    fault_type: str,
) -> None:
    with factory() as session, session.begin():
        account = session.scalar(
            select(ProviderAccount).where(ProviderAccount.public_id == account_public_id)
        )
        if account is None:
            raise RelayPayError(
                code="PROVIDER_ACCOUNT_NOT_FOUND",
                message="Provider account was not found",
                http_status=404,
            )
        directive = session.scalar(
            select(ProviderFaultDirective).where(
                ProviderFaultDirective.provider_account_id == account.id,
                ProviderFaultDirective.stable_key == stable_key,
                ProviderFaultDirective.fault_type == fault_type,
            )
        )
        if directive is None:
            session.add(
                ProviderFaultDirective(
                    provider_account_id=account.id,
                    stable_key=stable_key,
                    fault_type=fault_type,
                    remaining_uses=1,
                )
            )
        else:
            directive.remaining_uses += 1
