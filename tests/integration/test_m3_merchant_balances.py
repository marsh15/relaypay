import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from relaypay.contracts import EmptyCommand
from relaypay.database import build_engine, build_session_factory
from relaypay.idempotency import build_fingerprint, canonical_json_bytes
from relaypay.identity.models import Environment, Organisation, OrganisationMembership, User
from relaypay.identity.security import Principal, hash_password
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import LedgerAccount
from relaypay.ledger.service import post_capture_journal
from relaypay.merchant_balances.models import BalanceTransaction, MerchantAccount, SettlementItem
from relaypay.merchant_balances.service import derive_balances, run_settlement
from relaypay.payments.models import Authorization, Capture, Customer, PaymentIntent
from relaypay.provider_operations.models import ProviderOperation
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

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

        payload = EmptyCommand()
        fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template=(
                "/environments/{environment_id}/merchant-accounts/{merchant_account_id}/settlements"
            ),
            path_params={
                "environment_id": environment.public_id,
                "merchant_account_id": merchant_public_id,
            },
            body=payload,
        )
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
    finally:
        engine.dispose()
