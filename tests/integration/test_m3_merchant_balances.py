import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.contracts import EmptyCommand
from relaypay.database import build_engine, build_session_factory
from relaypay.idempotency import Fingerprint, build_fingerprint, canonical_json_bytes
from relaypay.identity.models import Environment, Organisation, OrganisationMembership, User
from relaypay.identity.security import Principal, hash_password
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.ledger.service import account, post_capture_journal, post_refund_journal
from relaypay.merchant_balances.models import BalanceTransaction, MerchantAccount, SettlementItem
from relaypay.merchant_balances.service import derive_balances, run_settlement
from relaypay.payments.models import Authorization, Capture, Customer, PaymentIntent, Refund
from relaypay.provider_operations.models import ProviderOperation
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"


def _identity() -> tuple[Engine, Principal, Environment]:
    engine = build_engine(DATABASE_URL, application_name="m3-merchant-balances-test")
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="M3 merchant balance test", status="ACTIVE"
        )
        user = User(
            email_normalized=f"m3-{uuid.uuid4().hex}@example.test",
            display_name="M3 admin",
            password_hash=hash_password("Synthetic-M3-Admin-Password!"),
            status="ACTIVE",
        )
        session.add_all([organisation, user])
        session.flush()
        session.add(
            OrganisationMembership(
                organisation_id=organisation.id,
                user_id=user.id,
                role="ORGANISATION_ADMIN",
                status="ACTIVE",
            )
        )
        environment = session.scalar(
            select(Environment).where(
                Environment.organisation_id == organisation.id,
                Environment.environment_type == "TEST",
            )
        )
        assert environment is not None
        session.add(
            LedgerAccount(
                organisation_id=organisation.id,
                environment_id=environment.id,
                code="PROVIDER_CLEARING_ASSET",
                name="Provider clearing",
                account_type="ASSET",
                currency="INR",
            )
        )
        principal = Principal(
            kind="SESSION",
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=None,
            environment_public_id=None,
            display_name=user.display_name,
            scopes=frozenset(),
            membership_role="ORGANISATION_ADMIN",
            user_id=user.id,
        )
    return engine, principal, environment


def _successful_capture(
    factory: sessionmaker[Session],
    principal: Principal,
    environment: Environment,
    amount: int,
) -> Capture:
    now = datetime.now(UTC)
    terminal = canonical_json_bytes({"status": "SUCCEEDED"})
    with factory() as session, session.begin():
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            merchant_customer_reference=f"m3-customer-{uuid.uuid4().hex}",
        )
        session.add(customer)
        session.flush()
        payment = PaymentIntent(
            public_id=new_public_id("pay"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            customer_id=customer.id,
            merchant_reference=f"m3-payment-{uuid.uuid4().hex}",
            amount=amount,
            currency="INR",
        )
        session.add(payment)
        session.flush()
        authorization_id = new_uuid()
        authorization_operation = ProviderOperation(
            id=new_uuid(),
            public_id=new_public_id("op"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=payment.id,
            resource_type="AUTHORIZATION",
            resource_id=authorization_id,
            kind="AUTHORIZE",
            stable_provider_key=f"authorize:{payment.public_id}",
            status="SUCCEEDED",
            terminal_http_status=200,
            terminal_response_headers={"Content-Type": "application/json"},
            terminal_response_bytes=terminal,
            terminal_response_sha256=hashlib.sha256(terminal).digest(),
            finalized_at=now,
        )
        authorization = Authorization(
            id=authorization_id,
            public_id=new_public_id("auth"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=payment.id,
            provider_operation_id=authorization_operation.id,
            amount=amount,
            currency="INR",
            status="SUCCEEDED",
            authorized_at=now,
        )
        session.add_all([authorization_operation, authorization])
        session.flush()
        capture_id = new_uuid()
        capture_operation = ProviderOperation(
            id=new_uuid(),
            public_id=new_public_id("op"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=payment.id,
            resource_type="CAPTURE",
            resource_id=capture_id,
            kind="CAPTURE",
            stable_provider_key=f"capture:{payment.public_id}",
            status="PROCESSING",
        )
        capture = Capture(
            id=capture_id,
            public_id=new_public_id("cap"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=payment.id,
            authorization_id=authorization.id,
            provider_operation_id=capture_operation.id,
            amount=amount,
            currency="INR",
            status="PROCESSING",
        )
        session.add_all([capture_operation, capture])
        session.flush()
        journal = post_capture_journal(
            session,
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            provider_operation_id=capture_operation.id,
            capture_id=capture.id,
            amount=amount,
        )
        capture.status = "SUCCEEDED"
        capture.captured_at = now
        capture.journal_id = journal.journal_id
    return capture


def _settlement_fingerprint(environment: Environment, merchant_public_id: str) -> Fingerprint:
    return build_fingerprint(
        api_version="admin-v1",
        method="POST",
        route_template=(
            "/environments/{environment_id}/merchant-accounts/{merchant_account_id}/settlements"
        ),
        path_params={
            "environment_id": environment.public_id,
            "merchant_account_id": merchant_public_id,
        },
        body=EmptyCommand(),
    )


def _default_merchant(
    factory: sessionmaker[Session], principal: Principal, environment: Environment
) -> MerchantAccount:
    with factory() as session, session.begin():
        merchant = session.scalar(
            select(MerchantAccount).where(
                MerchantAccount.organisation_id == principal.organisation_id,
                MerchantAccount.environment_id == environment.id,
                MerchantAccount.is_default.is_(True),
            )
        )
        assert merchant is not None
        return merchant


def _settle(
    factory: sessionmaker[Session],
    principal: Principal,
    environment: Environment,
    merchant: MerchantAccount,
) -> int:
    result = run_settlement(
        factory,
        principal=principal,
        environment_public_id=environment.public_id,
        merchant_public_id=merchant.public_id,
        idempotency_key=f"settlement-{uuid.uuid4().hex}",
        fingerprint=_settlement_fingerprint(environment, merchant.public_id),
        key_pepper="m3-funding-order-pepper",
    )
    return int(json.loads(result.body)["settledAmount"])


def _simulate_payout(
    factory: sessionmaker[Session],
    principal: Principal,
    environment: Environment,
    merchant: MerchantAccount,
    amount: int,
) -> None:
    with factory() as session, session.begin():
        journal = Journal(
            public_id=new_public_id("jrn"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            merchant_account_id=merchant.id,
            provider_operation_id=None,
            journal_type="COMPENSATION",
            reference_type="SYNTHETIC_PAYOUT",
            reference_id=new_uuid(),
            currency="INR",
        )
        session.add(journal)
        session.flush()
        available = account(
            session,
            principal.organisation_id,
            environment.id,
            "AVAILABLE_PAYABLE_LIABILITY",
            merchant_account_id=merchant.id,
        )
        clearing = account(
            session,
            principal.organisation_id,
            environment.id,
            "PAYOUT_CLEARING_ASSET",
            merchant_account_id=merchant.id,
        )
        session.add_all(
            [
                Posting(
                    organisation_id=principal.organisation_id,
                    environment_id=environment.id,
                    journal_id=journal.id,
                    account_id=available.id,
                    side="DEBIT",
                    amount=amount,
                    currency="INR",
                ),
                Posting(
                    organisation_id=principal.organisation_id,
                    environment_id=environment.id,
                    journal_id=journal.id,
                    account_id=clearing.id,
                    side="CREDIT",
                    amount=amount,
                    currency="INR",
                ),
            ]
        )


def _successful_refund(
    factory: sessionmaker[Session],
    principal: Principal,
    environment: Environment,
    capture: Capture,
    amount: int,
) -> None:
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        refund_id = new_uuid()
        operation = ProviderOperation(
            id=new_uuid(),
            public_id=new_public_id("op"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=capture.payment_intent_id,
            resource_type="REFUND",
            resource_id=refund_id,
            kind="REFUND",
            stable_provider_key=f"refund:{uuid.uuid4().hex}",
            status="PROCESSING",
        )
        refund = Refund(
            id=refund_id,
            public_id=new_public_id("ref"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=capture.payment_intent_id,
            capture_id=capture.id,
            provider_operation_id=operation.id,
            amount=amount,
            currency="INR",
            status="PROCESSING",
        )
        session.add_all([operation, refund])
        session.flush()
        journal = post_refund_journal(
            session,
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            provider_operation_id=operation.id,
            refund_id=refund.id,
            amount=amount,
        )
        refund.status = "SUCCEEDED"
        refund.refunded_at = now
        refund.journal_id = journal.journal_id


def test_capture_settlement_and_replay_are_posting_derived() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        capture = _successful_capture(factory, principal, environment, 100_000)
        with factory() as session, session.begin():
            merchant = session.scalar(
                select(MerchantAccount).where(
                    MerchantAccount.organisation_id == principal.organisation_id,
                    MerchantAccount.environment_id == environment.id,
                    MerchantAccount.is_default.is_(True),
                )
            )
            assert merchant is not None
            assert derive_balances(session, merchant).pending == 100_000
            merchant_public_id = merchant.public_id

        fingerprint = _settlement_fingerprint(environment, merchant_public_id)
        idempotency_key = f"settle-{uuid.uuid4().hex}"
        created = run_settlement(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            merchant_public_id=merchant_public_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            key_pepper="m3-test-pepper",
        )
        replayed = run_settlement(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            merchant_public_id=merchant_public_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            key_pepper="m3-test-pepper",
        )
        assert created.status_code == 201
        assert json.loads(created.body)["settledAmount"] == 100_000
        assert replayed.status_code == 200
        assert replayed.body == created.body
        assert replayed.replayed is True

        with factory() as session, session.begin():
            merchant = session.scalar(
                select(MerchantAccount).where(MerchantAccount.public_id == merchant_public_id)
            )
            assert merchant is not None
            balances = derive_balances(session, merchant)
            assert (balances.pending, balances.available, balances.receivable) == (
                0,
                100_000,
                0,
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SettlementItem)
                    .where(SettlementItem.capture_id == capture.id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(BalanceTransaction)
                    .where(BalanceTransaction.merchant_account_id == merchant.id)
                )
                == 2
            )
            transactions = list(
                session.scalars(
                    select(BalanceTransaction).where(
                        BalanceTransaction.merchant_account_id == merchant.id
                    )
                )
            )
            assert (
                sum(item.pending_delta for item in transactions),
                sum(item.available_delta for item in transactions),
                sum(item.receivable_delta for item in transactions),
            ) == (balances.pending, balances.available, balances.receivable)
    finally:
        engine.dispose()


def test_balance_transactions_are_immutable() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        _successful_capture(factory, principal, environment, 10_000)
        with (
            pytest.raises(DBAPIError, match="immutable"),
            factory() as session,
            session.begin(),
        ):
            session.execute(
                update(BalanceTransaction)
                .where(BalanceTransaction.organisation_id == principal.organisation_id)
                .values(pending_delta=1)
            )
    finally:
        engine.dispose()


def test_concurrent_settlements_move_each_capture_once() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        capture = _successful_capture(factory, principal, environment, 100_000)
        merchant = _default_merchant(factory, principal, environment)
        fingerprint = _settlement_fingerprint(environment, merchant.public_id)

        def settle(key: str) -> int:
            result = run_settlement(
                factory,
                principal=principal,
                environment_public_id=environment.public_id,
                merchant_public_id=merchant.public_id,
                idempotency_key=key,
                fingerprint=fingerprint,
                key_pepper="m3-concurrency-pepper",
            )
            return int(json.loads(result.body)["settledAmount"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            amounts = list(
                pool.map(
                    settle,
                    [f"settlement-a-{uuid.uuid4().hex}", f"settlement-b-{uuid.uuid4().hex}"],
                )
            )
        assert sorted(amounts) == [0, 100_000]
        with factory() as session, session.begin():
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SettlementItem)
                    .where(SettlementItem.capture_id == capture.id)
                )
                == 1
            )
    finally:
        engine.dispose()


def test_refund_after_payout_creates_receivable_and_future_settlement_offsets_it() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        first_capture = _successful_capture(factory, principal, environment, 100_000)
        merchant = _default_merchant(factory, principal, environment)
        assert _settle(factory, principal, environment, merchant) == 100_000
        _simulate_payout(factory, principal, environment, merchant, 80_000)
        _successful_refund(factory, principal, environment, first_capture, 100_000)

        with factory() as session, session.begin():
            current = session.get(MerchantAccount, merchant.id)
            assert current is not None
            balances = derive_balances(session, current)
            assert (balances.pending, balances.available, balances.receivable) == (0, 0, 80_000)
            refund_transaction = session.scalar(
                select(BalanceTransaction)
                .where(
                    BalanceTransaction.merchant_account_id == merchant.id,
                    BalanceTransaction.transaction_type == "REFUND",
                )
                .order_by(BalanceTransaction.created_at.desc())
            )
            assert refund_transaction is not None
            assert (
                refund_transaction.pending_delta,
                refund_transaction.available_delta,
                refund_transaction.receivable_delta,
            ) == (0, -20_000, 80_000)

        _successful_capture(factory, principal, environment, 50_000)
        assert _settle(factory, principal, environment, merchant) == 50_000
        with factory() as session, session.begin():
            current = session.get(MerchantAccount, merchant.id)
            assert current is not None
            balances = derive_balances(session, current)
            assert (balances.pending, balances.available, balances.receivable) == (0, 0, 30_000)
    finally:
        engine.dispose()


def test_merchant_balance_admin_api_preserves_exact_settlement_replay() -> None:
    engine, principal, environment = _identity()
    factory = build_session_factory(engine)
    try:
        _successful_capture(factory, principal, environment, 50_000)
        with factory() as session, session.begin():
            user = session.get(User, principal.user_id)
            assert user is not None
            email = user.email_normalized

        settings = Settings(
            APP_ENV="test",
            RELAYPAY_DATABASE_URL=DATABASE_URL,
            PROVIDER_DATABASE_URL=(
                "postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider"
            ),
            RECEIVER_DATABASE_URL=(
                "postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay"
            ),
            SESSION_SECRET="m3-session-secret-for-tests-at-least-32-bytes",
            CSRF_SECRET="m3-csrf-secret-for-tests-at-least-32-bytes",
            API_KEY_PEPPER="m3-api-key-pepper-for-tests-at-least-32-bytes",
            IDEMPOTENCY_KEY_PEPPER="m3-idempotency-pepper-for-tests",
            WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-m3-http-tests",
            PROVIDER_SIGNING_SECRET="m3-provider-signing-test",
            PROVIDER_CONTROL_SECRET="m3-provider-control-test",
            RECEIVER_WEBHOOK_SECRET="m3-receiver-webhook-test",
        )
        base = f"/api/admin/v1/environments/{environment.public_id}/merchant-accounts"
        with TestClient(create_app(settings)) as client:
            login = client.post(
                "/api/session/login",
                json={"email": email, "password": "Synthetic-M3-Admin-Password!"},
            )
            assert login.status_code == 200
            csrf = login.json()["csrfToken"]
            listed = client.get(base)
            assert listed.status_code == 200
            default = next(item for item in listed.json() if item["isDefault"])

            created_account = client.post(
                base,
                headers={"X-CSRF-Token": csrf},
                json={"reference": f"secondary-{uuid.uuid4().hex}", "name": "Secondary"},
            )
            assert created_account.status_code == 201
            assert created_account.json()["isDefault"] is False

            settlement_path = f"{base}/{default['id']}/settlements"
            headers = {
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"m3-http-settlement-{uuid.uuid4().hex}",
            }
            first = client.post(settlement_path, headers=headers, json={})
            replay = client.post(settlement_path, headers=headers, json={})
            assert first.status_code == 201, first.text
            assert replay.status_code == 200, replay.text
            assert replay.content == first.content
            assert replay.headers["Idempotency-Replayed"] == "true"

            balances = client.get(f"{base}/{default['id']}/balances")
            assert balances.status_code == 200
            assert balances.json() == {
                "merchantAccountId": default["id"],
                "currency": "INR",
                "pending": 0,
                "available": 50_000,
                "reserved": 0,
                "receivable": 0,
                "payoutEligible": 50_000,
            }
            transactions = client.get(f"{base}/{default['id']}/balance-transactions")
            assert transactions.status_code == 200
            assert [item["type"] for item in transactions.json()] == [
                "CAPTURE",
                "SETTLEMENT",
            ]

            missing_csrf = client.post(
                settlement_path,
                headers={"Idempotency-Key": f"missing-csrf-{uuid.uuid4().hex}"},
                json={},
            )
            assert missing_csrf.status_code == 403
            assert missing_csrf.json()["error"]["code"] == "CSRF_INVALID"
    finally:
        engine.dispose()
