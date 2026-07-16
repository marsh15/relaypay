import hashlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.ledger.service import post_capture_journal, post_refund_journal
from relaypay.payments.models import Authorization, Capture, Customer, PaymentIntent, Refund
from relaypay.provider_operations.models import ProviderOperation
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class LedgerContext:
    organisation_id: uuid.UUID
    payment_id: uuid.UUID
    authorization_id: uuid.UUID
    amount: int


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    url = os.getenv(
        "RELAYPAY_DATABASE_URL",
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
    )
    engine = build_engine(url, application_name="relaypay-ledger-tests")
    factory = build_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def ledger_context(session_factory: sessionmaker[Session]) -> LedgerContext:
    organisation_id = new_uuid()
    payment_id = new_uuid()
    authorization_id = new_uuid()
    operation_id = new_uuid()
    amount = 100_000
    terminal_bytes = canonical_json_bytes({"status": "SUCCEEDED"})
    now = datetime.now(UTC)
    with session_factory() as session, session.begin():
        organisation = Organisation(
            id=organisation_id,
            public_id=new_public_id("org"),
            name="Ledger test organisation",
            status="ACTIVE",
        )
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation_id,
            merchant_customer_reference=f"customer-{uuid.uuid4().hex}",
        )
        session.add_all([organisation, customer])
        session.flush()
        payment = PaymentIntent(
            id=payment_id,
            public_id=new_public_id("pay"),
            organisation_id=organisation_id,
            customer_id=customer.id,
            merchant_reference=f"payment-{uuid.uuid4().hex}",
            amount=amount,
            currency="INR",
        )
        authorization_operation = ProviderOperation(
            id=operation_id,
            public_id=new_public_id("op"),
            organisation_id=organisation_id,
            payment_intent_id=payment_id,
            resource_type="AUTHORIZATION",
            resource_id=authorization_id,
            kind="AUTHORIZE",
            stable_provider_key=f"authorize:{payment.public_id}",
            status="SUCCEEDED",
            attempt_count=0,
            apply_failure_count=0,
            terminal_http_status=200,
            terminal_response_headers={"Content-Type": "application/json"},
            terminal_response_bytes=terminal_bytes,
            terminal_response_sha256=hashlib.sha256(terminal_bytes).digest(),
            finalized_at=now,
        )
        authorization = Authorization(
            id=authorization_id,
            public_id=new_public_id("auth"),
            organisation_id=organisation_id,
            payment_intent_id=payment_id,
            provider_operation_id=operation_id,
            amount=amount,
            currency="INR",
            status="SUCCEEDED",
            authorized_at=now,
        )
        session.add(payment)
        session.flush([payment])
        session.add_all([authorization_operation, authorization])
        session.flush([authorization_operation, authorization])
        session.add_all(
            [
                LedgerAccount(
                    organisation_id=organisation_id,
                    code="PROVIDER_CLEARING_ASSET",
                    name="Provider clearing",
                    account_type="ASSET",
                    currency="INR",
                ),
                LedgerAccount(
                    organisation_id=organisation_id,
                    code="MERCHANT_PAYABLE_LIABILITY",
                    name="Merchant payable",
                    account_type="LIABILITY",
                    currency="INR",
                ),
            ]
        )
    return LedgerContext(organisation_id, payment_id, authorization_id, amount)


def _capture_operation(
    session: Session, context: LedgerContext
) -> tuple[ProviderOperation, Capture]:
    capture_id = new_uuid()
    operation = ProviderOperation(
        id=new_uuid(),
        public_id=new_public_id("op"),
        organisation_id=context.organisation_id,
        payment_intent_id=context.payment_id,
        resource_type="CAPTURE",
        resource_id=capture_id,
        kind="CAPTURE",
        stable_provider_key=f"capture:{uuid.uuid4().hex}",
        status="PROCESSING",
        attempt_count=0,
        apply_failure_count=0,
    )
    capture = Capture(
        id=capture_id,
        public_id=new_public_id("cap"),
        organisation_id=context.organisation_id,
        payment_intent_id=context.payment_id,
        authorization_id=context.authorization_id,
        provider_operation_id=operation.id,
        amount=context.amount,
        currency="INR",
        status="PROCESSING",
    )
    session.add_all([operation, capture])
    session.flush([operation, capture])
    return operation, capture


def _terminalize_capture(
    operation: ProviderOperation, capture: Capture, journal_id: uuid.UUID
) -> None:
    now = datetime.now(UTC)
    response = canonical_json_bytes({"status": "SUCCEEDED"})
    operation.status = "SUCCEEDED"
    operation.terminal_http_status = 200
    operation.terminal_response_headers = {"Content-Type": "application/json"}
    operation.terminal_response_bytes = response
    operation.terminal_response_sha256 = hashlib.sha256(response).digest()
    operation.finalized_at = now
    capture.status = "SUCCEEDED"
    capture.captured_at = now
    capture.journal_id = journal_id


def _refund_operation(
    session: Session, context: LedgerContext, capture: Capture
) -> tuple[ProviderOperation, Refund]:
    refund_id = new_uuid()
    operation = ProviderOperation(
        id=new_uuid(),
        public_id=new_public_id("op"),
        organisation_id=context.organisation_id,
        payment_intent_id=context.payment_id,
        resource_type="REFUND",
        resource_id=refund_id,
        kind="REFUND",
        stable_provider_key=f"refund:ref_{refund_id.hex}",
        status="PROCESSING",
        attempt_count=0,
        apply_failure_count=0,
    )
    refund = Refund(
        id=refund_id,
        public_id=f"ref_{refund_id.hex}",
        organisation_id=context.organisation_id,
        payment_intent_id=context.payment_id,
        capture_id=capture.id,
        provider_operation_id=operation.id,
        amount=25_000,
        currency="INR",
        status="PROCESSING",
    )
    session.add_all([operation, refund])
    session.flush([operation, refund])
    return operation, refund


def test_capture_and_refund_templates_are_balanced(
    session_factory: sessionmaker[Session], ledger_context: LedgerContext
) -> None:
    with session_factory() as session, session.begin():
        capture_operation, capture_resource = _capture_operation(session, ledger_context)
        capture = post_capture_journal(
            session,
            organisation_id=ledger_context.organisation_id,
            provider_operation_id=capture_operation.id,
            capture_id=capture_resource.id,
            amount=ledger_context.amount,
        )
        _terminalize_capture(capture_operation, capture_resource, capture.journal_id)
        refund_operation, refund_resource = _refund_operation(
            session, ledger_context, capture_resource
        )
        refund = post_refund_journal(
            session,
            organisation_id=ledger_context.organisation_id,
            provider_operation_id=refund_operation.id,
            refund_id=refund_resource.id,
            amount=refund_resource.amount,
        )
    assert capture.debit_total == capture.credit_total == 100_000
    assert refund.debit_total == refund.credit_total == 25_000


def test_unbalanced_journal_fails_at_commit(
    session_factory: sessionmaker[Session], ledger_context: LedgerContext
) -> None:
    with (
        pytest.raises(IntegrityError, match="unbalanced"),
        session_factory() as session,
        session.begin(),
    ):
        operation, capture = _capture_operation(session, ledger_context)
        accounts = session.scalars(
            select(LedgerAccount).where(
                LedgerAccount.organisation_id == ledger_context.organisation_id
            )
        ).all()
        journal = Journal(
            public_id=new_public_id("jrn"),
            organisation_id=ledger_context.organisation_id,
            provider_operation_id=operation.id,
            journal_type="CAPTURE",
            reference_type="CAPTURE",
            reference_id=capture.id,
            currency="INR",
        )
        session.add(journal)
        session.flush()
        session.add_all(
            [
                Posting(
                    organisation_id=ledger_context.organisation_id,
                    journal_id=journal.id,
                    account_id=accounts[0].id,
                    side="DEBIT",
                    amount=ledger_context.amount,
                    currency="INR",
                ),
                Posting(
                    organisation_id=ledger_context.organisation_id,
                    journal_id=journal.id,
                    account_id=accounts[1].id,
                    side="CREDIT",
                    amount=ledger_context.amount - 1,
                    currency="INR",
                ),
            ]
        )


def test_journal_requires_at_least_two_postings(
    session_factory: sessionmaker[Session], ledger_context: LedgerContext
) -> None:
    with (
        pytest.raises(IntegrityError, match="requires at least two postings"),
        session_factory() as session,
        session.begin(),
    ):
        operation, capture = _capture_operation(session, ledger_context)
        account = session.scalar(
            select(LedgerAccount).where(
                LedgerAccount.organisation_id == ledger_context.organisation_id,
                LedgerAccount.code == "PROVIDER_CLEARING_ASSET",
            )
        )
        assert account is not None
        journal = Journal(
            public_id=new_public_id("jrn"),
            organisation_id=ledger_context.organisation_id,
            provider_operation_id=operation.id,
            journal_type="CAPTURE",
            reference_type="CAPTURE",
            reference_id=capture.id,
            currency="INR",
        )
        session.add(journal)
        session.flush()
        session.add(
            Posting(
                organisation_id=ledger_context.organisation_id,
                journal_id=journal.id,
                account_id=account.id,
                side="DEBIT",
                amount=ledger_context.amount,
                currency="INR",
            )
        )


def test_posted_journal_and_postings_are_immutable(
    session_factory: sessionmaker[Session], ledger_context: LedgerContext
) -> None:
    with session_factory() as session, session.begin():
        operation, capture = _capture_operation(session, ledger_context)
        result = post_capture_journal(
            session,
            organisation_id=ledger_context.organisation_id,
            provider_operation_id=operation.id,
            capture_id=capture.id,
            amount=ledger_context.amount,
        )
    with (
        pytest.raises(DBAPIError, match="immutable"),
        session_factory() as session,
        session.begin(),
    ):
        session.execute(
            text("UPDATE journals SET currency = 'INR' WHERE id = :id"),
            {"id": result.journal_id},
        )
    with (
        pytest.raises(DBAPIError, match="immutable"),
        session_factory() as session,
        session.begin(),
    ):
        session.execute(
            text("DELETE FROM postings WHERE journal_id = :id"), {"id": result.journal_id}
        )


def test_non_inr_posting_is_rejected(
    session_factory: sessionmaker[Session], ledger_context: LedgerContext
) -> None:
    with (
        pytest.raises(IntegrityError, match="currency"),
        session_factory() as session,
        session.begin(),
    ):
        operation, capture = _capture_operation(session, ledger_context)
        account = session.scalar(
            select(LedgerAccount).where(
                LedgerAccount.organisation_id == ledger_context.organisation_id
            )
        )
        assert account is not None
        journal = Journal(
            public_id=new_public_id("jrn"),
            organisation_id=ledger_context.organisation_id,
            provider_operation_id=operation.id,
            journal_type="CAPTURE",
            reference_type="CAPTURE",
            reference_id=capture.id,
            currency="INR",
        )
        session.add(journal)
        session.flush()
        session.execute(
            text(
                """
                INSERT INTO postings
                  (id, organisation_id, journal_id, account_id, side, amount, currency)
                VALUES
                  (:id, :organisation_id, :journal_id, :account_id, 'DEBIT', 100, 'USD')
                """
            ),
            {
                "id": new_uuid(),
                "organisation_id": ledger_context.organisation_id,
                "journal_id": journal.id,
                "account_id": account.id,
            },
        )
