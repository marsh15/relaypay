import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import BaseModel
from relaypay.contracts import EmptyCommand
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.idempotency import Fingerprint, build_fingerprint
from relaypay.merchant_balances.service import derive_balances
from relaypay.mock_bank.models import BankAccount, BankEffect
from relaypay.mock_bank.service import (
    apply_transfer,
    configure_fault,
    lookup_transfer,
    parse_command,
)
from relaypay.payouts.models import (
    Payout,
    PayoutAttempt,
    PayoutAttemptEvidence,
    PayoutEvent,
    PayoutHistory,
    PayoutReservationHistory,
)
from relaypay.payouts.service import (
    create_beneficiary,
    create_payout,
    create_retry,
    dispatch_payout_attempt,
)
from relaypay.provider_operations.service_types import ProviderObservation
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_m3_merchant_balances import (
    _default_merchant,
    _identity,
    _settle,
    _successful_capture,
)

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]

BANK_DATABASE_URL = "postgresql+psycopg://bank_app:bank_app_dev@localhost:55432/bank"
BANK_ACCOUNT = "bank_m4_test"
BANK_SECRET = "m4-synthetic-bank-secret"


class PayoutBody(BaseModel):
    merchant_account_id: str
    beneficiary_id: str
    amount: int


class LocalBankTransport:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        reply = apply_transfer(
            self.factory,
            command=parse_command(request_bytes),
            signing_secret=BANK_SECRET,
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        reply = lookup_transfer(
            self.factory,
            account_public_id=account_id,
            stable_key=stable_key,
            signing_secret=BANK_SECRET,
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)


def _fingerprint(environment_id: str, body: PayoutBody) -> Fingerprint:
    return build_fingerprint(
        api_version="admin-v1",
        method="POST",
        route_template="/environments/{environment_id}/payouts",
        path_params={"environment_id": environment_id},
        body=body,
    )


def test_lost_response_is_lookup_only_and_consumes_one_reservation() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    bank_engine = build_engine(BANK_DATABASE_URL, application_name="m4-payout-bank-test")
    bank_factory = sessionmaker(bind=bank_engine, expire_on_commit=False, autobegin=False)
    try:
        _successful_capture(factory, principal, environment, 100_000)
        merchant = _default_merchant(factory, principal, environment)
        assert _settle(factory, principal, environment, merchant) == 100_000
        with factory() as session, session.begin():
            beneficiary = create_beneficiary(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=f"m4-{uuid.uuid4().hex}",
                display_name="Synthetic beneficiary",
                bank_account_reference=f"synthetic-bank-{uuid.uuid4().hex}",
            )
        body = PayoutBody(
            merchant_account_id=merchant.public_id,
            beneficiary_id=beneficiary.public_id,
            amount=60_000,
        )
        created = create_payout(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            merchant_public_id=merchant.public_id,
            beneficiary_public_id=beneficiary.public_id,
            amount=body.amount,
            idempotency_key=f"m4-{uuid.uuid4().hex}",
            fingerprint=_fingerprint(environment.public_id, body),
            key_pepper="m4-test-pepper",
        )
        payout_id = json.loads(created.body)["id"]
        stable_key = f"payout:{payout_id}:attempt:1"

        with bank_factory() as session, session.begin():
            if (
                session.scalar(select(BankAccount).where(BankAccount.public_id == BANK_ACCOUNT))
                is None
            ):
                session.add(
                    BankAccount(
                        public_id=BANK_ACCOUNT,
                        name="M4 synthetic account",
                        signing_secret_digest=hashlib.sha256(BANK_SECRET.encode()).digest(),
                    )
                )
        configure_fault(
            bank_factory,
            account_public_id=BANK_ACCOUNT,
            stable_key=stable_key,
            fault_type="LOSE_RESPONSE",
        )
        transport = LocalBankTransport(bank_factory)
        dispatch_payout_attempt(
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=transport,
        )
        with factory() as session, session.begin():
            payout = session.scalar(select(Payout).where(Payout.public_id == payout_id))
            current_merchant = session.get(type(merchant), merchant.id)
            assert payout is not None and current_merchant is not None
            assert payout.status == "REQUIRES_REVIEW"
            balances = derive_balances(session, current_merchant)
            assert (balances.available, balances.reserved, balances.payout_eligible) == (
                100_000,
                60_000,
                40_000,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    lambda _: dispatch_payout_attempt(
                        factory,
                        payout_public_id=payout_id,
                        bank_account_id=BANK_ACCOUNT,
                        bank_signing_secret=BANK_SECRET,
                        transport=transport,
                    ),
                    range(2),
                )
            )

        with factory() as session, session.begin():
            payout = session.scalar(select(Payout).where(Payout.public_id == payout_id))
            current_merchant = session.get(type(merchant), merchant.id)
            assert payout is not None and current_merchant is not None
            assert payout.status == "SUCCEEDED"
            balances = derive_balances(session, current_merchant)
            assert (balances.available, balances.reserved) == (40_000, 0)
            attempt = session.scalar(
                select(PayoutAttempt).where(PayoutAttempt.payout_id == payout.id)
            )
            assert attempt is not None and attempt.attempt_number == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(PayoutEvent)
                    .where(PayoutEvent.payout_id == payout.id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(PayoutAttemptEvidence)
                    .where(
                        PayoutAttemptEvidence.payout_attempt_id == attempt.id,
                        PayoutAttemptEvidence.evidence_kind == "MUTATION_SEND",
                    )
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.sum(PayoutReservationHistory.amount_delta)).where(
                        PayoutReservationHistory.payout_id == payout.id
                    )
                )
                == 0
            )
        with bank_factory() as session, session.begin():
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(BankEffect)
                    .where(BankEffect.stable_key == stable_key)
                )
                == 1
            )
    finally:
        bank_engine.dispose()
        engine.dispose()


def test_payout_cannot_reserve_more_than_available() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        _successful_capture(factory, principal, environment, 50_000)
        merchant = _default_merchant(factory, principal, environment)
        _settle(factory, principal, environment, merchant)
        with factory() as session, session.begin():
            beneficiary = create_beneficiary(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=f"m4-limit-{uuid.uuid4().hex}",
                display_name="Synthetic limit beneficiary",
                bank_account_reference=f"synthetic-limit-{uuid.uuid4().hex}",
            )
        body = PayoutBody(
            merchant_account_id=merchant.public_id,
            beneficiary_id=beneficiary.public_id,
            amount=40_000,
        )

        def reserve(key: str) -> str:
            try:
                create_payout(
                    factory,
                    principal=principal,
                    environment_public_id=environment.public_id,
                    merchant_public_id=merchant.public_id,
                    beneficiary_public_id=beneficiary.public_id,
                    amount=40_000,
                    idempotency_key=key,
                    fingerprint=_fingerprint(environment.public_id, body),
                    key_pepper="m4-concurrency-pepper",
                )
                return "created"
            except RelayPayError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, [uuid.uuid4().hex, uuid.uuid4().hex]))
        assert sorted(outcomes) == ["INSUFFICIENT_PAYOUT_BALANCE", "created"]
    finally:
        engine.dispose()


def test_verified_decline_releases_and_explicit_retry_is_numbered() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    bank_engine = build_engine(BANK_DATABASE_URL, application_name="m4-payout-retry-test")
    bank_factory = sessionmaker(bind=bank_engine, expire_on_commit=False, autobegin=False)
    try:
        _successful_capture(factory, principal, environment, 75_000)
        merchant = _default_merchant(factory, principal, environment)
        _settle(factory, principal, environment, merchant)
        with factory() as session, session.begin():
            beneficiary = create_beneficiary(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                reference=f"m4-retry-{uuid.uuid4().hex}",
                display_name="Synthetic retry beneficiary",
                bank_account_reference=f"synthetic-retry-{uuid.uuid4().hex}",
            )
        body = PayoutBody(
            merchant_account_id=merchant.public_id,
            beneficiary_id=beneficiary.public_id,
            amount=25_000,
        )
        created = create_payout(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            merchant_public_id=merchant.public_id,
            beneficiary_public_id=beneficiary.public_id,
            amount=body.amount,
            idempotency_key=uuid.uuid4().hex,
            fingerprint=_fingerprint(environment.public_id, body),
            key_pepper="m4-retry-pepper",
        )
        payout_id = json.loads(created.body)["id"]
        with bank_factory() as session, session.begin():
            if (
                session.scalar(select(BankAccount).where(BankAccount.public_id == BANK_ACCOUNT))
                is None
            ):
                session.add(
                    BankAccount(
                        public_id=BANK_ACCOUNT,
                        name="M4 synthetic account",
                        signing_secret_digest=hashlib.sha256(BANK_SECRET.encode()).digest(),
                    )
                )
        configure_fault(
            bank_factory,
            account_public_id=BANK_ACCOUNT,
            stable_key=f"payout:{payout_id}:attempt:1",
            fault_type="DECLINE",
        )
        transport = LocalBankTransport(bank_factory)
        dispatch_payout_attempt(
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=transport,
        )
        retry_fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template="/environments/{environment_id}/payouts/{payout_id}/attempts",
            path_params={"environment_id": environment.public_id, "payout_id": payout_id},
            body=EmptyCommand(),
        )
        retry = create_retry(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            payout_public_id=payout_id,
            idempotency_key=uuid.uuid4().hex,
            fingerprint=retry_fingerprint,
            key_pepper="m4-retry-pepper",
        )
        assert json.loads(retry.body)["attemptNumber"] == 2
        dispatch_payout_attempt(
            factory,
            payout_public_id=payout_id,
            bank_account_id=BANK_ACCOUNT,
            bank_signing_secret=BANK_SECRET,
            transport=transport,
        )
        with factory() as session, session.begin():
            payout = session.scalar(select(Payout).where(Payout.public_id == payout_id))
            assert payout is not None and payout.status == "SUCCEEDED"
            attempts = list(
                session.scalars(
                    select(PayoutAttempt)
                    .where(PayoutAttempt.payout_id == payout.id)
                    .order_by(PayoutAttempt.attempt_number)
                )
            )
            assert [(item.attempt_number, item.status) for item in attempts] == [
                (1, "FAILED"),
                (2, "SUCCEEDED"),
            ]
            history = list(
                session.scalars(
                    select(PayoutHistory)
                    .where(PayoutHistory.payout_id == payout.id)
                    .order_by(PayoutHistory.created_at, PayoutHistory.id)
                )
            )
            assert [item.reason_code for item in history] == [
                "PAYOUT_CREATED",
                "BENEFICIARY_REJECTED",
                "ADMIN_RETRY",
                "VERIFIED_BANK_SUCCESS",
            ]
            assert (
                session.scalar(
                    select(func.sum(PayoutReservationHistory.amount_delta)).where(
                        PayoutReservationHistory.payout_id == payout.id
                    )
                )
                == 0
            )
    finally:
        bank_engine.dispose()
        engine.dispose()
