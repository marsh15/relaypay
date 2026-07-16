import os
import uuid

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.ledger.service import post_capture_journal, post_refund_journal
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory():  # type: ignore[no-untyped-def]
    url = os.getenv(
        "RELAYPAY_DATABASE_URL",
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
    )
    engine = build_engine(url, application_name="relaypay-tests")
    factory = build_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def organisation_id(session_factory) -> uuid.UUID:  # type: ignore[no-untyped-def]
    organisation_id = new_uuid()
    with session_factory() as session, session.begin():
        session.add(
            Organisation(
                id=organisation_id,
                public_id=new_public_id("org"),
                name="Ledger test organisation",
                status="ACTIVE",
            )
        )
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
    return organisation_id


def test_capture_and_refund_templates_are_balanced(session_factory, organisation_id) -> None:  # type: ignore[no-untyped-def]
    with session_factory() as session, session.begin():
        capture = post_capture_journal(
            session,
            organisation_id=organisation_id,
            provider_operation_id=new_uuid(),
            capture_id=new_uuid(),
            amount=100_000,
        )
    with session_factory() as session, session.begin():
        refund = post_refund_journal(
            session,
            organisation_id=organisation_id,
            provider_operation_id=new_uuid(),
            refund_id=new_uuid(),
            amount=25_000,
        )
    assert capture.debit_total == capture.credit_total == 100_000
    assert refund.debit_total == refund.credit_total == 25_000


def test_unbalanced_journal_fails_at_commit(session_factory, organisation_id) -> None:  # type: ignore[no-untyped-def]
    with (
        pytest.raises(IntegrityError, match="unbalanced"),
        session_factory() as session,
        session.begin(),
    ):
        accounts = session.scalars(
            select(LedgerAccount).where(LedgerAccount.organisation_id == organisation_id)
        ).all()
        journal = Journal(
            public_id=new_public_id("jrn"),
            organisation_id=organisation_id,
            provider_operation_id=new_uuid(),
            journal_type="CAPTURE",
            reference_type="CAPTURE",
            reference_id=new_uuid(),
            currency="INR",
        )
        session.add(journal)
        session.flush()
        session.add_all(
            [
                Posting(
                    organisation_id=organisation_id,
                    journal_id=journal.id,
                    account_id=accounts[0].id,
                    side="DEBIT",
                    amount=100,
                    currency="INR",
                ),
                Posting(
                    organisation_id=organisation_id,
                    journal_id=journal.id,
                    account_id=accounts[1].id,
                    side="CREDIT",
                    amount=99,
                    currency="INR",
                ),
            ]
        )


def test_journal_requires_at_least_two_postings(session_factory, organisation_id) -> None:  # type: ignore[no-untyped-def]
    with (
        pytest.raises(IntegrityError, match="requires at least two postings"),
        session_factory() as session,
        session.begin(),
    ):
        account = session.scalar(
            select(LedgerAccount).where(
                LedgerAccount.organisation_id == organisation_id,
                LedgerAccount.code == "PROVIDER_CLEARING_ASSET",
            )
        )
        assert account is not None
        journal = Journal(
            public_id=new_public_id("jrn"),
            organisation_id=organisation_id,
            provider_operation_id=new_uuid(),
            journal_type="CAPTURE",
            reference_type="CAPTURE",
            reference_id=new_uuid(),
            currency="INR",
        )
        session.add(journal)
        session.flush()
        session.add(
            Posting(
                organisation_id=organisation_id,
                journal_id=journal.id,
                account_id=account.id,
                side="DEBIT",
                amount=100,
                currency="INR",
            )
        )


def test_posted_journal_and_postings_are_immutable(session_factory, organisation_id) -> None:  # type: ignore[no-untyped-def]
    with session_factory() as session, session.begin():
        result = post_capture_journal(
            session,
            organisation_id=organisation_id,
            provider_operation_id=new_uuid(),
            capture_id=new_uuid(),
            amount=100,
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


def test_non_inr_posting_is_rejected(session_factory, organisation_id) -> None:  # type: ignore[no-untyped-def]
    with (
        pytest.raises(IntegrityError, match="currency"),
        session_factory() as session,
        session.begin(),
    ):
        account = session.scalar(
            select(LedgerAccount).where(LedgerAccount.organisation_id == organisation_id)
        )
        assert account is not None
        journal = Journal(
            public_id=new_public_id("jrn"),
            organisation_id=organisation_id,
            provider_operation_id=new_uuid(),
            journal_type="CAPTURE",
            reference_type="CAPTURE",
            reference_id=new_uuid(),
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
                "organisation_id": organisation_id,
                "journal_id": journal.id,
                "account_id": account.id,
            },
        )
