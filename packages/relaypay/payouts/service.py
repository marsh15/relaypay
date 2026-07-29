import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx2
from sqlalchemy import Integer, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import Fingerprint, canonical_json_bytes, digest_secret
from relaypay.identity.models import Environment
from relaypay.identity.security import Principal
from relaypay.identity.service import append_audit, require_organisation_admin
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.service import account, add_balance_transaction, post_journal
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.merchant_balances.service import derive_balances
from relaypay.payouts.models import (
    Beneficiary,
    Payout,
    PayoutAttempt,
    PayoutAttemptEvidence,
    PayoutEvent,
    PayoutHistory,
    PayoutReservationHistory,
)
from relaypay.provider_operations.service_types import ProviderObservation


class BankTransport(Protocol):
    def mutate(self, request_bytes: bytes) -> ProviderObservation: ...

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation: ...


class HTTPBankTransport:
    def __init__(self, *, base_url: str, timeout_seconds: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        response = httpx2.post(
            f"{self._base_url}/v1/transfers",
            content=request_bytes,
            headers={"Content-Type": "application/json"},
            timeout=self._timeout,
        )
        return ProviderObservation(response.status_code, response.content, dict(response.headers))

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        response = httpx2.get(
            f"{self._base_url}/v1/transfers/{stable_key}",
            params={"account_id": account_id},
            timeout=self._timeout,
        )
        return ProviderObservation(response.status_code, response.content, dict(response.headers))


@dataclass(frozen=True, slots=True)
class CommandResult:
    status_code: int
    body: bytes
    replayed: bool


@dataclass(frozen=True, slots=True)
class PreparedAttempt:
    payout_id: uuid.UUID
    attempt_id: uuid.UUID
    stable_key: str
    request_bytes: bytes
    lookup_only: bool
    terminal: bool


@dataclass(frozen=True, slots=True)
class BankClassification:
    terminal: bool
    succeeded: bool
    ambiguous: bool
    signature_valid: bool | None
    code: str | None


def _environment(session: Session, principal: Principal, public_id: str) -> Environment:
    environment = session.scalar(
        select(Environment).where(
            Environment.organisation_id == principal.organisation_id,
            Environment.public_id == public_id,
            Environment.status == "ACTIVE",
        )
    )
    if environment is None:
        raise not_found("Environment")
    return environment


def _merchant(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    public_id: str,
    lock: bool = False,
) -> MerchantAccount:
    statement = select(MerchantAccount).where(
        MerchantAccount.organisation_id == organisation_id,
        MerchantAccount.environment_id == environment_id,
        MerchantAccount.public_id == public_id,
        MerchantAccount.status == "ACTIVE",
    )
    merchant = session.scalar(statement.with_for_update() if lock else statement)
    if merchant is None:
        raise not_found("Merchant account")
    return merchant


def create_beneficiary(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    reference: str,
    display_name: str,
    bank_account_reference: str,
) -> Beneficiary:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    beneficiary = Beneficiary(
        id=new_uuid(),
        public_id=new_public_id("bnf"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        reference=reference,
        display_name=display_name,
        bank_account_reference=bank_account_reference,
        currency="INR",
        status="ACTIVE",
    )
    session.add(beneficiary)
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="BENEFICIARY_CREATED",
        target_type="BENEFICIARY",
        target_id=beneficiary.public_id,
        details={"reference": reference},
    )
    return beneficiary


def _reservation(
    session: Session,
    payout: Payout,
    *,
    action: str,
    amount_delta: int,
    reason_code: str,
) -> None:
    sequence = (
        int(
            session.scalar(
                select(
                    func.coalesce(func.max(PayoutReservationHistory.sequence), 0).cast(Integer)
                ).where(PayoutReservationHistory.payout_id == payout.id)
            )
            or 0
        )
        + 1
    )
    session.add(
        PayoutReservationHistory(
            organisation_id=payout.organisation_id,
            environment_id=payout.environment_id,
            merchant_account_id=payout.merchant_account_id,
            payout_id=payout.id,
            sequence=sequence,
            action=action,
            amount_delta=amount_delta,
            reason_code=reason_code,
        )
    )


def _history(
    session: Session,
    payout: Payout,
    *,
    attempt: PayoutAttempt | None,
    from_status: str | None,
    to_status: str,
    reason_code: str,
    actor_type: str,
) -> None:
    session.add(
        PayoutHistory(
            organisation_id=payout.organisation_id,
            environment_id=payout.environment_id,
            payout_id=payout.id,
            payout_attempt_id=attempt.id if attempt else None,
            from_status=from_status,
            to_status=to_status,
            reason_code=reason_code,
            actor_type=actor_type,
        )
    )


def _payout_body(
    payout: Payout,
    beneficiary: Beneficiary,
    attempt: PayoutAttempt,
) -> bytes:
    return canonical_json_bytes(
        {
            "amount": payout.amount,
            "attemptNumber": attempt.attempt_number,
            "beneficiaryId": beneficiary.public_id,
            "currency": payout.currency,
            "failureCode": payout.failure_code,
            "id": payout.public_id,
            "reviewReason": payout.review_reason,
            "stableProviderKey": attempt.stable_provider_key,
            "status": payout.status,
        }
    )


def _create_payout_transaction(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    merchant_public_id: str,
    beneficiary_public_id: str,
    amount: int,
    key_digest: bytes,
    fingerprint: Fingerprint,
) -> CommandResult:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    existing = session.scalar(
        select(Payout).where(
            Payout.organisation_id == principal.organisation_id,
            Payout.environment_id == environment.id,
            Payout.idempotency_key_digest == key_digest,
        )
    )
    if existing is not None:
        if existing.fingerprint_sha256 != fingerprint.sha256:
            raise RelayPayError(
                code="IDEMPOTENCY_KEY_REUSED",
                message="The idempotency key was already used for a different request",
                http_status=409,
            )
        if existing.response_bytes is None or existing.response_http_status is None:
            raise RelayPayError(
                code="PAYOUT_IN_PROGRESS",
                message="The payout command is still processing",
                http_status=409,
            )
        return CommandResult(existing.response_http_status, existing.response_bytes, True)
    beneficiary = session.scalar(
        select(Beneficiary).where(
            Beneficiary.organisation_id == principal.organisation_id,
            Beneficiary.environment_id == environment.id,
            Beneficiary.public_id == beneficiary_public_id,
            Beneficiary.status == "ACTIVE",
        )
    )
    if beneficiary is None:
        raise not_found("Beneficiary")
    merchant = _merchant(
        session,
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        public_id=merchant_public_id,
        lock=True,
    )
    balances = derive_balances(session, merchant)
    if amount <= 0 or amount > balances.payout_eligible:
        raise RelayPayError(
            code="INSUFFICIENT_PAYOUT_BALANCE",
            message="The payout exceeds the positive payout-eligible balance",
            http_status=409,
            details={"payoutEligible": balances.payout_eligible},
        )
    payout = Payout(
        id=new_uuid(),
        public_id=new_public_id("pot"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        merchant_account_id=merchant.id,
        beneficiary_id=beneficiary.id,
        amount=amount,
        currency="INR",
        status="PROCESSING",
        idempotency_key_digest=key_digest,
        fingerprint_sha256=fingerprint.sha256,
    )
    session.add(payout)
    session.flush()
    attempt = PayoutAttempt(
        id=new_uuid(),
        public_id=new_public_id("pat"),
        organisation_id=payout.organisation_id,
        environment_id=payout.environment_id,
        payout_id=payout.id,
        attempt_number=1,
        stable_provider_key=f"payout:{payout.public_id}:attempt:1",
        status="PREPARED",
    )
    session.add(attempt)
    _reservation(
        session,
        payout,
        action="RESERVED",
        amount_delta=amount,
        reason_code="PAYOUT_CREATED",
    )
    _history(
        session,
        payout,
        attempt=attempt,
        from_status=None,
        to_status="PROCESSING",
        reason_code="PAYOUT_CREATED",
        actor_type="ADMIN",
    )
    body = _payout_body(payout, beneficiary, attempt)
    payout.response_http_status = 202
    payout.response_bytes = body
    payout.response_sha256 = hashlib.sha256(body).digest()
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="PAYOUT_CREATED",
        target_type="PAYOUT",
        target_id=payout.public_id,
        details={"amount": amount, "merchantAccountId": merchant.public_id},
    )
    return CommandResult(202, body, False)


def create_payout(
    factory: sessionmaker[Session],
    *,
    principal: Principal,
    environment_public_id: str,
    merchant_public_id: str,
    beneficiary_public_id: str,
    amount: int,
    idempotency_key: str,
    fingerprint: Fingerprint,
    key_pepper: str,
) -> CommandResult:
    key_digest = digest_secret(idempotency_key, key_pepper)
    try:
        with factory() as session, session.begin():
            return _create_payout_transaction(
                session,
                principal=principal,
                environment_public_id=environment_public_id,
                merchant_public_id=merchant_public_id,
                beneficiary_public_id=beneficiary_public_id,
                amount=amount,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )
    except IntegrityError:
        with factory() as session, session.begin():
            return _create_payout_transaction(
                session,
                principal=principal,
                environment_public_id=environment_public_id,
                merchant_public_id=merchant_public_id,
                beneficiary_public_id=beneficiary_public_id,
                amount=amount,
                key_digest=key_digest,
                fingerprint=fingerprint,
            )


def create_retry(
    factory: sessionmaker[Session],
    *,
    principal: Principal,
    environment_public_id: str,
    payout_public_id: str,
    idempotency_key: str,
    fingerprint: Fingerprint,
    key_pepper: str,
) -> CommandResult:
    require_organisation_admin(principal)
    key_digest = digest_secret(idempotency_key, key_pepper)
    with factory() as session, session.begin():
        environment = _environment(session, principal, environment_public_id)
        existing = session.scalar(
            select(PayoutAttempt).where(
                PayoutAttempt.organisation_id == principal.organisation_id,
                PayoutAttempt.environment_id == environment.id,
                PayoutAttempt.idempotency_key_digest == key_digest,
            )
        )
        if existing is not None:
            if existing.fingerprint_sha256 != fingerprint.sha256:
                raise RelayPayError(
                    code="IDEMPOTENCY_KEY_REUSED",
                    message="The idempotency key was already used for a different request",
                    http_status=409,
                )
            assert existing.response_bytes is not None
            return CommandResult(201, existing.response_bytes, True)
        payout = session.scalar(
            select(Payout)
            .where(
                Payout.organisation_id == principal.organisation_id,
                Payout.environment_id == environment.id,
                Payout.public_id == payout_public_id,
            )
            .with_for_update()
        )
        if payout is None:
            raise not_found("Payout")
        if payout.status != "FAILED":
            raise RelayPayError(
                code="PAYOUT_RETRY_NOT_ALLOWED",
                message="Only a verified failed payout can be retried explicitly",
                http_status=409,
            )
        merchant = session.scalar(
            select(MerchantAccount)
            .where(MerchantAccount.id == payout.merchant_account_id)
            .with_for_update()
        )
        if merchant is None:
            raise RuntimeError("payout merchant account is missing")
        if derive_balances(session, merchant).payout_eligible < payout.amount:
            raise RelayPayError(
                code="INSUFFICIENT_PAYOUT_BALANCE",
                message="The payout amount cannot be reacquired",
                http_status=409,
            )
        number = (
            int(
                session.scalar(
                    select(func.max(PayoutAttempt.attempt_number).cast(Integer)).where(
                        PayoutAttempt.payout_id == payout.id
                    )
                )
                or 0
            )
            + 1
        )
        attempt = PayoutAttempt(
            id=new_uuid(),
            public_id=new_public_id("pat"),
            organisation_id=payout.organisation_id,
            environment_id=payout.environment_id,
            payout_id=payout.id,
            attempt_number=number,
            stable_provider_key=f"payout:{payout.public_id}:attempt:{number}",
            status="PREPARED",
            idempotency_key_digest=key_digest,
            fingerprint_sha256=fingerprint.sha256,
        )
        session.add(attempt)
        beneficiary = session.get(Beneficiary, payout.beneficiary_id)
        if beneficiary is None:
            raise RuntimeError("payout beneficiary is missing")
        prior = payout.status
        payout.status = "PROCESSING"
        payout.failure_code = None
        payout.review_reason = None
        payout.completed_at = None
        _reservation(
            session,
            payout,
            action="RESERVED",
            amount_delta=payout.amount,
            reason_code="ADMIN_RETRY",
        )
        _history(
            session,
            payout,
            attempt=attempt,
            from_status=prior,
            to_status="PROCESSING",
            reason_code="ADMIN_RETRY",
            actor_type="ADMIN",
        )
        body = _payout_body(payout, beneficiary, attempt)
        attempt.response_bytes = body
        append_audit(
            session,
            principal=principal,
            environment_id=environment.id,
            action="PAYOUT_RETRY_CREATED",
            target_type="PAYOUT",
            target_id=payout.public_id,
            details={"attemptNumber": number},
        )
        return CommandResult(201, body, False)


def prepare_attempt_send(
    factory: sessionmaker[Session],
    *,
    payout_public_id: str,
    bank_account_id: str,
) -> PreparedAttempt:
    with factory() as session, session.begin():
        payout = session.scalar(
            select(Payout).where(Payout.public_id == payout_public_id).with_for_update()
        )
        if payout is None:
            raise not_found("Payout")
        attempt = session.scalar(
            select(PayoutAttempt)
            .where(PayoutAttempt.payout_id == payout.id)
            .order_by(PayoutAttempt.attempt_number.desc())
            .limit(1)
            .with_for_update()
        )
        if attempt is None:
            raise RuntimeError("payout attempt is missing")
        if attempt.status in {"SUCCEEDED", "FAILED"}:
            return PreparedAttempt(
                payout.id, attempt.id, attempt.stable_provider_key, b"", True, True
            )
        if attempt.last_sent_at is not None:
            if attempt.request_bytes is None:
                raise RuntimeError("sent payout attempt is missing request bytes")
            return PreparedAttempt(
                payout.id,
                attempt.id,
                attempt.stable_provider_key,
                attempt.request_bytes,
                True,
                False,
            )
        beneficiary = session.get(Beneficiary, payout.beneficiary_id)
        if beneficiary is None:
            raise RuntimeError("payout beneficiary is missing")
        request_bytes = canonical_json_bytes(
            {
                "accountId": bank_account_id,
                "amount": payout.amount,
                "beneficiaryReference": beneficiary.bank_account_reference,
                "currency": payout.currency,
                "payoutReference": payout.public_id,
                "stableKey": attempt.stable_provider_key,
            }
        )
        request_sha = hashlib.sha256(request_bytes).digest()
        now = datetime.now(UTC)
        attempt.request_bytes = request_bytes
        attempt.request_sha256 = request_sha
        attempt.last_sent_at = now
        attempt.status = "SENT"
        session.add(
            PayoutAttemptEvidence(
                organisation_id=payout.organisation_id,
                environment_id=payout.environment_id,
                payout_attempt_id=attempt.id,
                sequence=1,
                evidence_kind="MUTATION_SEND",
                state="SENT",
                request_sha256=request_sha,
            )
        )
    return PreparedAttempt(
        payout.id, attempt.id, attempt.stable_provider_key, request_bytes, False, False
    )


def _classify(
    observation: ProviderObservation | None,
    *,
    expected_request: bytes,
    signing_secret: str,
) -> BankClassification:
    if observation is None or observation.status_code >= 500 or not observation.body:
        return BankClassification(False, False, True, None, "BANK_TRANSPORT_AMBIGUOUS")
    provided = next(
        (value for key, value in observation.headers.items() if key.lower() == "x-bank-signature"),
        None,
    )
    expected_signature = hmac.new(
        signing_secret.encode(), observation.body, hashlib.sha256
    ).hexdigest()
    signature_valid = provided is not None and hmac.compare_digest(provided, expected_signature)
    if not signature_valid:
        return BankClassification(False, False, True, False, "BANK_SIGNATURE_INVALID")
    try:
        response = json.loads(observation.body)
        request = json.loads(expected_request)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return BankClassification(False, False, True, True, "BANK_RESPONSE_INVALID")
    expected_fields = {
        "accountId": request["accountId"],
        "amount": request["amount"],
        "beneficiaryReference": request["beneficiaryReference"],
        "currency": request["currency"],
        "payoutReference": request["payoutReference"],
        "stableKey": request["stableKey"],
    }
    if any(response.get(key) != value for key, value in expected_fields.items()):
        return BankClassification(False, False, True, True, "BANK_RESPONSE_MISMATCHED")
    if response.get("outcome") == "SUCCEEDED":
        return BankClassification(True, True, False, True, None)
    if response.get("outcome") == "DECLINED" and response.get("declineCode"):
        return BankClassification(True, False, False, True, str(response["declineCode"]))
    return BankClassification(False, False, True, True, "BANK_OUTCOME_PENDING")


def _append_evidence(
    session: Session,
    payout: Payout,
    attempt: PayoutAttempt,
    *,
    lookup_only: bool,
    observation: ProviderObservation | None,
    classification: BankClassification,
) -> None:
    if attempt.request_sha256 is None:
        raise RuntimeError("sent payout attempt is missing its request digest")
    sequence = (
        int(
            session.scalar(
                select(func.max(PayoutAttemptEvidence.sequence).cast(Integer)).where(
                    PayoutAttemptEvidence.payout_attempt_id == attempt.id
                )
            )
            or 0
        )
        + 1
    )
    session.add(
        PayoutAttemptEvidence(
            organisation_id=payout.organisation_id,
            environment_id=payout.environment_id,
            payout_attempt_id=attempt.id,
            sequence=sequence,
            evidence_kind="LOOKUP" if lookup_only else "MUTATION_RESULT",
            state=(
                "TRANSPORT_ERROR"
                if observation is None
                else "VALIDATION_REJECTED"
                if classification.ambiguous
                else "RESPONSE_RECEIVED"
            ),
            request_sha256=attempt.request_sha256,
            response_http_status=observation.status_code if observation else None,
            response_bytes=observation.body if observation else None,
            response_sha256=hashlib.sha256(observation.body).digest() if observation else None,
            bank_signature_valid=classification.signature_valid,
            classification=(
                "VERIFIED_SUCCESS"
                if classification.succeeded
                else "VERIFIED_BUSINESS_DECLINE"
                if classification.terminal
                else "AMBIGUOUS"
            ),
            safe_error_code=classification.code,
            completed_at=datetime.now(UTC),
        )
    )


def record_bank_observation(
    factory: sessionmaker[Session],
    *,
    prepared: PreparedAttempt,
    observation: ProviderObservation | None,
    classification: BankClassification,
) -> None:
    with factory() as session, session.begin():
        payout = session.scalar(
            select(Payout).where(Payout.id == prepared.payout_id).with_for_update()
        )
        attempt = session.scalar(
            select(PayoutAttempt).where(PayoutAttempt.id == prepared.attempt_id).with_for_update()
        )
        if payout is None or attempt is None:
            raise RuntimeError("payout graph is missing")
        if attempt.status in {"SUCCEEDED", "FAILED"}:
            return
        _append_evidence(
            session,
            payout,
            attempt,
            lookup_only=prepared.lookup_only,
            observation=observation,
            classification=classification,
        )
        prior = payout.status
        beneficiary = session.get(Beneficiary, payout.beneficiary_id)
        if beneficiary is None:
            raise RuntimeError("payout beneficiary is missing")
        if classification.ambiguous:
            attempt.status = "AMBIGUOUS"
            attempt.review_reason = classification.code
            payout.status = "REQUIRES_REVIEW"
            payout.review_reason = classification.code
            _history(
                session,
                payout,
                attempt=attempt,
                from_status=prior,
                to_status="REQUIRES_REVIEW",
                reason_code=classification.code or "BANK_AMBIGUOUS",
                actor_type="RECOVERY_WORKER" if prepared.lookup_only else "PAYOUT_WORKER",
            )
        elif not classification.succeeded:
            attempt.status = "FAILED"
            attempt.failure_code = classification.code
            payout.status = "FAILED"
            payout.failure_code = classification.code
            payout.review_reason = None
            payout.completed_at = datetime.now(UTC)
            _reservation(
                session,
                payout,
                action="RELEASED",
                amount_delta=-payout.amount,
                reason_code="VERIFIED_BANK_FAILURE",
            )
            _history(
                session,
                payout,
                attempt=attempt,
                from_status=prior,
                to_status="FAILED",
                reason_code=classification.code or "BANK_DECLINED",
                actor_type="FINALIZER",
            )
        else:
            journal = post_journal(
                session,
                organisation_id=payout.organisation_id,
                environment_id=payout.environment_id,
                merchant_account_id=payout.merchant_account_id,
                provider_operation_id=None,
                journal_type="PAYOUT",
                reference_type="PAYOUT",
                reference_id=payout.id,
                entries=[
                    (
                        account(
                            session,
                            payout.organisation_id,
                            payout.environment_id,
                            "AVAILABLE_PAYABLE_LIABILITY",
                            merchant_account_id=payout.merchant_account_id,
                        ),
                        "DEBIT",
                        payout.amount,
                    ),
                    (
                        account(
                            session,
                            payout.organisation_id,
                            payout.environment_id,
                            "PAYOUT_CLEARING_ASSET",
                            merchant_account_id=payout.merchant_account_id,
                        ),
                        "CREDIT",
                        payout.amount,
                    ),
                ],
            )
            add_balance_transaction(
                session,
                organisation_id=payout.organisation_id,
                environment_id=payout.environment_id,
                merchant_account_id=payout.merchant_account_id,
                journal_id=journal.journal_id,
                transaction_type="PAYOUT",
                available_delta=-payout.amount,
                payout_clearing_delta=payout.amount,
            )
            _reservation(
                session,
                payout,
                action="CONSUMED",
                amount_delta=-payout.amount,
                reason_code="VERIFIED_BANK_SUCCESS",
            )
            attempt.status = "SUCCEEDED"
            payout.status = "SUCCEEDED"
            payout.review_reason = None
            payout.journal_id = journal.journal_id
            payout.completed_at = datetime.now(UTC)
            event_bytes = canonical_json_bytes(
                {
                    "amount": payout.amount,
                    "currency": payout.currency,
                    "eventId": f"evt_{payout.public_id}",
                    "payoutId": payout.public_id,
                    "type": "payout.succeeded.v1",
                }
            )
            session.add(
                PayoutEvent(
                    id=new_uuid(),
                    public_id=new_public_id("evt"),
                    organisation_id=payout.organisation_id,
                    environment_id=payout.environment_id,
                    payout_id=payout.id,
                    event_type="payout.succeeded.v1",
                    event_bytes=event_bytes,
                    event_sha256=hashlib.sha256(event_bytes).digest(),
                )
            )
            _history(
                session,
                payout,
                attempt=attempt,
                from_status=prior,
                to_status="SUCCEEDED",
                reason_code="VERIFIED_BANK_SUCCESS",
                actor_type="FINALIZER",
            )
        body = _payout_body(payout, beneficiary, attempt)
        payout.response_http_status = 200
        payout.response_bytes = body
        payout.response_sha256 = hashlib.sha256(body).digest()


def dispatch_payout_attempt(
    factory: sessionmaker[Session],
    *,
    payout_public_id: str,
    bank_account_id: str,
    bank_signing_secret: str,
    transport: BankTransport,
) -> None:
    prepared = prepare_attempt_send(
        factory, payout_public_id=payout_public_id, bank_account_id=bank_account_id
    )
    if prepared.terminal:
        return
    try:
        observation = (
            transport.lookup(account_id=bank_account_id, stable_key=prepared.stable_key)
            if prepared.lookup_only
            else transport.mutate(prepared.request_bytes)
        )
    except Exception:
        observation = None
    classification = _classify(
        observation,
        expected_request=prepared.request_bytes,
        signing_secret=bank_signing_secret,
    )
    record_bank_observation(
        factory,
        prepared=prepared,
        observation=observation,
        classification=classification,
    )


def list_payouts(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
) -> list[Payout]:
    environment = _environment(session, principal, environment_public_id)
    return list(
        session.scalars(
            select(Payout)
            .where(
                Payout.organisation_id == principal.organisation_id,
                Payout.environment_id == environment.id,
            )
            .order_by(Payout.created_at, Payout.id)
        )
    )


def list_beneficiaries(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
) -> list[Beneficiary]:
    environment = _environment(session, principal, environment_public_id)
    return list(
        session.scalars(
            select(Beneficiary)
            .where(
                Beneficiary.organisation_id == principal.organisation_id,
                Beneficiary.environment_id == environment.id,
            )
            .order_by(Beneficiary.created_at, Beneficiary.id)
        )
    )


def run_payout_batch(
    factory: sessionmaker[Session],
    *,
    bank_account_id: str,
    bank_signing_secret: str,
    transport: BankTransport,
    limit: int = 25,
) -> int:
    with factory() as session, session.begin():
        payout_ids = list(
            session.scalars(
                select(Payout.public_id)
                .join(PayoutAttempt, PayoutAttempt.payout_id == Payout.id)
                .where(PayoutAttempt.status.in_(("PREPARED", "AMBIGUOUS")))
                .order_by(PayoutAttempt.updated_at, PayoutAttempt.id)
                .limit(limit)
            )
        )
    for payout_id in payout_ids:
        dispatch_payout_attempt(
            factory,
            payout_public_id=payout_id,
            bank_account_id=bank_account_id,
            bank_signing_secret=bank_signing_secret,
            transport=transport,
        )
    return len(payout_ids)
