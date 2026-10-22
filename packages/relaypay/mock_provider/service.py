import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_uuid
from relaypay.mock_provider.chaos import EffectChaos, account_profile, effect_chaos
from relaypay.mock_provider.models import (
    ProviderAccount,
    ProviderEffect,
    ProviderFaultDirective,
    ProviderStatementExport,
    ProviderStatementItem,
)
from relaypay.mock_provider.webhooks import enqueue_effect_webhook

UNAVAILABLE_BODY = b'{"error":"PROVIDER_UNAVAILABLE"}'


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


@dataclass(frozen=True, slots=True)
class StatementExportResult:
    public_id: str
    source_reference: str
    body: bytes
    sha256: str
    item_count: int


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


def not_found_body(account_public_id: str, stable_key: str) -> bytes:
    return canonical_json_bytes(
        {"accountId": account_public_id, "outcome": "NOT_FOUND", "stableKey": stable_key}
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
    webhooks: bool = False,
) -> ProviderReply:
    """Create (or replay) the effect for a stable key.

    ``webhooks`` enqueues the effect's signed webhook in the same transaction. With no one-shot
    fault directive, the account's chaos profile (if any) decides the outcome and response.
    """
    request_bytes = command.canonical_bytes()
    request_sha = hashlib.sha256(request_bytes).digest()
    selected_fault: str | None = None
    chaos: EffectChaos | None = None
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
            else:
                profile = account_profile(session, account.id)
                chaos = effect_chaos(profile, command.stable_key) if profile else None
                if chaos is not None and chaos.error_before:
                    return ProviderReply(
                        503, UNAVAILABLE_BODY, {"Content-Type": "application/json"}
                    )

            outcome = "PENDING" if selected_fault == "PENDING" else "SUCCEEDED"
            if selected_fault == "DECLINE":
                outcome = "DECLINED"
            elif chaos is not None:
                outcome = chaos.outcome
            decline_code = "DO_NOT_HONOR" if outcome == "DECLINED" else None
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
            if webhooks:
                session.flush([effect])
                enqueue_effect_webhook(
                    session,
                    account_id=account.id,
                    effect_id=effect.id,
                    stable_key=command.stable_key,
                    effect_body=correct_body,
                    now=datetime.now(UTC),
                )
    except RelayPayError:
        raise
    except IntegrityError as error:
        raise RelayPayError(
            code="PROVIDER_EFFECT_CONFLICT",
            message="A concurrent request selected the stable provider effect",
            http_status=409,
        ) from error

    if chaos is not None:
        if chaos.slow_ms:
            time.sleep(chaos.slow_ms / 1000)
        if chaos.lose_response:
            return ProviderReply(599, b"", {})
        if chaos.error_after:
            return ProviderReply(503, UNAVAILABLE_BODY, {"Content-Type": "application/json"})
    if selected_fault == "LOSE_RESPONSE":
        return ProviderReply(599, b"", {})
    if selected_fault == "SLOW_RESPONSE":
        time.sleep(3)
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
            # A signed, identity-bound "not found": RelayPay may treat it as proof the effect
            # never happened once its grace period has passed (ADR-011).
            account_exists = session.scalar(
                select(ProviderAccount.id).where(ProviderAccount.public_id == account_public_id)
            )
            if account_exists is None:
                raise RelayPayError(
                    code="PROVIDER_ACCOUNT_NOT_FOUND",
                    message="Provider account was not found",
                    http_status=404,
                )
            return _reply(
                not_found_body(account_public_id, stable_key), signing_secret, status_code=404
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


def export_statement(
    factory: sessionmaker[Session],
    *,
    account_public_id: str,
    source_reference: str,
    period_start: datetime,
    period_end: datetime,
) -> StatementExportResult:
    if period_end <= period_start:
        raise RelayPayError(
            code="INVALID_STATEMENT_PERIOD",
            message="Statement period end must be after its start",
            http_status=422,
        )
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
        existing = session.scalar(
            select(ProviderStatementExport).where(
                ProviderStatementExport.provider_account_id == account.id,
                ProviderStatementExport.source_reference == source_reference,
            )
        )
        if existing is not None:
            count = session.scalar(
                select(func.count())
                .select_from(ProviderStatementItem)
                .where(ProviderStatementItem.statement_export_id == existing.id)
            )
            return StatementExportResult(
                public_id=existing.public_id,
                source_reference=existing.source_reference,
                body=existing.raw_bytes,
                sha256=existing.raw_sha256.hex(),
                item_count=count or 0,
            )
        effects = list(
            session.scalars(
                select(ProviderEffect)
                .where(
                    ProviderEffect.provider_account_id == account.id,
                    ProviderEffect.created_at >= period_start,
                    ProviderEffect.created_at < period_end,
                )
                .order_by(ProviderEffect.created_at, ProviderEffect.id)
            )
        )
        item_values = [
            {
                "amount": effect.amount,
                "currency": effect.currency,
                "occurredAt": (effect.completed_at or effect.created_at).isoformat(),
                "operationKind": effect.operation_kind,
                "providerItemId": str(effect.id),
                "stableKey": effect.stable_key,
                "status": effect.outcome,
            }
            for effect in effects
        ]
        body = json.dumps({"items": item_values}, separators=(",", ":"), sort_keys=True).encode()
        digest = hashlib.sha256(body).digest()
        export = ProviderStatementExport(
            public_id=f"stmt_{new_uuid().hex}",
            provider_account_id=account.id,
            source_reference=source_reference,
            source_format="JSON",
            period_start=period_start,
            period_end=period_end,
            raw_bytes=body,
            raw_sha256=digest,
        )
        session.add(export)
        session.flush()
        session.add_all(
            [
                ProviderStatementItem(
                    statement_export_id=export.id,
                    ordinal=ordinal,
                    provider_item_id=value["providerItemId"],
                    stable_key=value["stableKey"],
                    operation_kind=value["operationKind"],
                    amount=value["amount"],
                    currency=value["currency"],
                    provider_status=value["status"],
                    occurred_at=effect.completed_at or effect.created_at,
                )
                for ordinal, (effect, value) in enumerate(zip(effects, item_values, strict=True), 1)
            ]
        )
        return StatementExportResult(
            public_id=export.public_id,
            source_reference=source_reference,
            body=body,
            sha256=digest.hex(),
            item_count=len(effects),
        )
