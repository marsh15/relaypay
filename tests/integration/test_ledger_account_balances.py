"""Migration 0035 (ADR-014): stored running totals equal the ledger, whatever writes it.

``ledger_account_balances`` is a cache of the postings kept by a database trigger. These tests post
through the real ``post_journal`` (randomly, from several threads at once, and in transactions
that roll back or fail at commit) and check every stored row against the ledger sum. They also
show that nothing but the trigger can write the table, and that the migration's backfill equals
the ledger and refuses to finish when it does not.
"""

import importlib.util
import logging
import random
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Literal

import pytest
from alembic import command
from relaypay.ids import new_uuid
from relaypay.ledger.models import LedgerAccount, LedgerAccountBalance, Posting
from relaypay.ledger.service import (
    account,
    balance_drift,
    credit_balance,
    post_journal,
    recompute_balance,
)
from relaypay.merchant_balances.service import create_merchant_account
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import World, balances, build_world, fund_available, paid_payment

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]

MERCHANT_CODES = (
    "PENDING_PAYABLE_LIABILITY",
    "AVAILABLE_PAYABLE_LIABILITY",
    "PAYOUT_CLEARING_ASSET",
    "MERCHANT_RECEIVABLE_ASSET",
)
# AVAILABLE debits are checked against payout coverage at commit (0020); the random journals
# below only credit it, so every one of them commits.
FREE_CODES = ("PENDING_PAYABLE_LIABILITY", "PAYOUT_CLEARING_ASSET", "MERCHANT_RECEIVABLE_ASSET")
Side = Literal["DEBIT", "CREDIT"]
GUARD = "ledger_account_balances_guard"
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "relaypay" / "versions"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("ledger-balance-tests", pool_size=10) as session_factory:
        yield session_factory


def _accounts(session: Session, world: World) -> dict[str, LedgerAccount]:
    merchant = {
        code: account(
            session,
            world.organisation_id,
            world.environment_id,
            code,
            merchant_account_id=world.merchant_account_id,
        )
        for code in MERCHANT_CODES
    }
    clearing = account(
        session, world.organisation_id, world.environment_id, "PROVIDER_CLEARING_ASSET"
    )
    return {**merchant, "PROVIDER_CLEARING_ASSET": clearing}


def _post(
    session: Session,
    world: World,
    entries: list[tuple[LedgerAccount, Side, int]],
) -> uuid.UUID:
    result = post_journal(
        session,
        organisation_id=world.organisation_id,
        environment_id=world.environment_id,
        merchant_account_id=world.merchant_account_id,
        provider_operation_id=None,
        journal_type="COMPENSATION",  # type: ignore[arg-type]
        reference_type="test_ledger_balance",
        reference_id=new_uuid(),
        entries=entries,
    )
    session.flush()
    return result.journal_id


def _stored(session: Session, account_id: uuid.UUID) -> tuple[int, int, int] | None:
    row = session.execute(
        select(
            LedgerAccountBalance.credit_total,
            LedgerAccountBalance.debit_total,
            LedgerAccountBalance.posting_count,
        ).where(LedgerAccountBalance.account_id == account_id)
    ).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


def _from_postings(session: Session, account_id: uuid.UUID) -> tuple[int, int, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(Posting.amount).filter(Posting.side == "CREDIT"), 0),
            func.coalesce(func.sum(Posting.amount).filter(Posting.side == "DEBIT"), 0),
            func.count(),
        ).where(Posting.account_id == account_id)
    ).one()
    return (int(row[0]), int(row[1]), int(row[2]))


def _assert_world_matches_ledger(factory: sessionmaker[Session], world: World) -> None:
    with factory() as session, session.begin():
        accounts = _accounts(session, world)
        for code in MERCHANT_CODES:
            account_id = accounts[code].id
            assert _stored(session, account_id) == _from_postings(session, account_id), code
            assert credit_balance(session, account_id) == recompute_balance(session, account_id)
        # Platform accounts are not cached; their balance is still the ledger sum.
        clearing = accounts["PROVIDER_CLEARING_ASSET"].id
        assert _stored(session, clearing) is None
        assert credit_balance(session, clearing) == recompute_balance(session, clearing)
        ours = {item.id for item in accounts.values()}
        assert [d for d in balance_drift(session) if d.account_id in ours] == []


def test_merchant_accounts_open_with_a_zero_row_and_platform_accounts_have_none(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="balance-open")
    with factory() as session, session.begin():
        extra = create_merchant_account(
            session,
            organisation_id=world.organisation_id,
            environment_id=world.environment_id,
            reference="second",
            name="Second",
            is_default=False,
        )
        session.flush()
        opened = session.scalars(
            select(LedgerAccount).where(LedgerAccount.merchant_account_id == extra.id)
        ).all()
        assert len(opened) == len(MERCHANT_CODES)
        for ledger_account in opened:
            assert _stored(session, ledger_account.id) == (0, 0, 0)
    _assert_world_matches_ledger(factory, world)


def test_random_postings_keep_every_stored_total_equal_to_the_ledger(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="balance-random")
    paid_payment(factory, world, FakeProvider(), amount=250_000)
    generator = random.Random(20261120)  # noqa: S311 - deterministic test data
    with factory() as session, session.begin():
        accounts = _accounts(session, world)
    codes = (*FREE_CODES, "PROVIDER_CLEARING_ASSET")
    for _ in range(40):
        with factory() as session, session.begin():
            accounts = _accounts(session, world)
            for _ in range(generator.randint(1, 4)):
                amount = generator.randint(1, 50_000)
                debit, credit = generator.sample(codes, 2)
                entries: list[tuple[LedgerAccount, Side, int]] = [
                    (accounts[debit], "DEBIT", amount)
                ]
                split = generator.randint(0, amount - 1) if amount > 1 else 0
                if split:
                    entries.append((accounts[credit], "CREDIT", split))
                    entries.append(
                        (accounts["AVAILABLE_PAYABLE_LIABILITY"], "CREDIT", amount - split)
                    )
                else:
                    entries.append((accounts[credit], "CREDIT", amount))
                generator.shuffle(entries)
                _post(session, world, entries)
    _assert_world_matches_ledger(factory, world)
    # derive_balances reads the stored rows; it still agrees with the ledger sums.
    with factory() as session, session.begin():
        accounts = _accounts(session, world)
        pending = recompute_balance(session, accounts["PENDING_PAYABLE_LIABILITY"].id)
        available = recompute_balance(session, accounts["AVAILABLE_PAYABLE_LIABILITY"].id)
        receivable = -recompute_balance(session, accounts["MERCHANT_RECEIVABLE_ASSET"].id)
    derived = balances(factory, world)
    assert (derived.pending, derived.available, derived.receivable) == (
        pending,
        available,
        receivable,
    )


def test_concurrent_postings_to_one_account_lose_no_increment(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="balance-threads")
    threads, journals_per_thread, amount = 8, 25, 1_000
    with factory() as session, session.begin():
        accounts = _accounts(session, world)
        before = _stored(session, accounts["PENDING_PAYABLE_LIABILITY"].id)
    assert before == (0, 0, 0)
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait()
            for n in range(journals_per_thread):
                with factory() as session, session.begin():
                    mine = _accounts(session, world)
                    # Every journal credits the shared PENDING row; half also touch a second
                    # merchant row, always after it, as capture and payout journals do.
                    entries: list[tuple[LedgerAccount, Side, int]] = [
                        (mine["PROVIDER_CLEARING_ASSET"], "DEBIT", amount),
                        (mine["PENDING_PAYABLE_LIABILITY"], "CREDIT", amount),
                    ]
                    if (index + n) % 2:
                        entries.append((mine["MERCHANT_RECEIVABLE_ASSET"], "DEBIT", 7))
                        entries.append((mine["PROVIDER_CLEARING_ASSET"], "CREDIT", 7))
                    _post(session, world, entries)
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    assert errors == []
    total = threads * journals_per_thread
    with factory() as session, session.begin():
        pending = _stored(session, accounts["PENDING_PAYABLE_LIABILITY"].id)
        receivable = _stored(session, accounts["MERCHANT_RECEIVABLE_ASSET"].id)
    assert pending == (total * amount, 0, total)
    assert receivable == (0, 7 * (total // 2), total // 2)
    _assert_world_matches_ledger(factory, world)


def test_rolled_back_or_failed_posting_leaves_the_stored_total_unchanged(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="balance-rollback")
    fund_available(factory, world, FakeProvider(), 40_000)
    with factory() as session, session.begin():
        accounts = _accounts(session, world)
        pending_id = accounts["PENDING_PAYABLE_LIABILITY"].id
        before = _stored(session, pending_id)
    assert before is not None

    # Rolled back by the caller: the cache moved inside the transaction, and back out with it.
    with factory() as session:
        session.begin()
        mine = _accounts(session, world)
        _post(
            session,
            world,
            [
                (mine["PROVIDER_CLEARING_ASSET"], "DEBIT", 5_000),
                (mine["PENDING_PAYABLE_LIABILITY"], "CREDIT", 5_000),
            ],
        )
        inside = _stored(session, pending_id)
        assert inside == (before[0] + 5_000, before[1], before[2] + 1)
        session.rollback()

    # Refused at commit by the deferred journal-balance trigger (0001): a balanced journal plus
    # one stray credit. The stray posting reached the cache before the commit failed.
    with (
        pytest.raises(IntegrityError, match="unbalanced"),
        factory() as session,
        session.begin(),
    ):
        mine = _accounts(session, world)
        journal_id = _post(
            session,
            world,
            [
                (mine["PROVIDER_CLEARING_ASSET"], "DEBIT", 4_000),
                (mine["PENDING_PAYABLE_LIABILITY"], "CREDIT", 4_000),
            ],
        )
        session.add(
            Posting(
                organisation_id=world.organisation_id,
                environment_id=world.environment_id,
                journal_id=journal_id,
                account_id=pending_id,
                side="CREDIT",
                amount=1_000,
                currency="INR",
            )
        )
        session.flush()
        assert _stored(session, pending_id) == (before[0] + 5_000, before[1], before[2] + 2)

    # Refused at commit by the payout-coverage trigger (0020): an AVAILABLE overdraft.
    with (
        pytest.raises(IntegrityError, match="exceed available payable"),
        factory() as session,
        session.begin(),
    ):
        mine = _accounts(session, world)
        _post(
            session,
            world,
            [
                (mine["AVAILABLE_PAYABLE_LIABILITY"], "DEBIT", 40_001),
                (mine["MERCHANT_RECEIVABLE_ASSET"], "CREDIT", 40_001),
            ],
        )

    with factory() as session, session.begin():
        assert _stored(session, pending_id) == before
    _assert_world_matches_ledger(factory, world)


def _first_balance_row(session: Session) -> uuid.UUID:
    account_id = session.scalar(select(LedgerAccountBalance.account_id).limit(1))
    assert account_id is not None
    return account_id


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ledger_account_balances SET credit_total = credit_total + 1 WHERE account_id = :id",
        "DELETE FROM ledger_account_balances WHERE account_id = :id",
        "INSERT INTO ledger_account_balances (account_id, organisation_id, environment_id) "
        "SELECT :id, organisation_id, environment_id FROM ledger_account_balances "
        "WHERE account_id = :id",
    ],
    ids=["update", "delete", "insert"],
)
def test_app_role_cannot_write_the_stored_balances(
    factory: sessionmaker[Session], statement: str
) -> None:
    build_world(factory, label="balance-app-role")
    with factory() as session, session.begin():
        account_id = _first_balance_row(session)
        assert session.scalar(text("SELECT current_user")) == "relaypay_app"
    with pytest.raises(ProgrammingError) as raised, factory() as session, session.begin():
        session.execute(text(statement), {"id": account_id})
    assert getattr(raised.value.orig, "sqlstate", None) == "42501"  # insufficient_privilege


def _migrator_url() -> str:
    from scripts.verify_m2_upgrade import RELAYPAY_CONFIG_PATH, _base_url

    return _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")


def test_owner_cannot_write_the_stored_balances_outside_the_trigger(
    factory: sessionmaker[Session],
) -> None:
    build_world(factory, label="balance-owner")
    with factory() as session, session.begin():
        account_id = _first_balance_row(session)
    engine = create_engine(_migrator_url())
    try:
        for statement in (
            "UPDATE ledger_account_balances SET posting_count = posting_count + 1 "
            "WHERE account_id = :id",
            "DELETE FROM ledger_account_balances WHERE account_id = :id",
        ):
            with (
                pytest.raises(DBAPIError, match="maintained only by the posting trigger"),
                engine.begin() as connection,
            ):
                connection.execute(text(statement), {"id": account_id})
    finally:
        engine.dispose()


def test_backfill_equals_the_ledger_and_refuses_a_wrong_total(
    factory: sessionmaker[Session],
) -> None:
    """Upgrade a scratch schema, write money through the services, rebuild the cache with the
    0035 backfill and compare; then corrupt one row and run the migration's own check."""
    from scripts.verify_m2_upgrade import _create_schema

    migration = _migration_0035()
    base_url = _migrator_url()
    schema = f"balance_backfill_{uuid.uuid4().hex}"
    _create_schema(base_url, schema)
    with _logging_preserved():
        _backfill_round_trip(migration, base_url, schema)


def _backfill_round_trip(migration: ModuleType, base_url: str, schema: str) -> None:
    from relaypay.database import build_session_factory

    from scripts.verify_m2_upgrade import (
        RELAYPAY_CONFIG_PATH,
        _configuration,
        _drop_schema,
        _schema_url,
    )

    try:
        url = _schema_url(base_url, schema)
        config = _configuration(RELAYPAY_CONFIG_PATH, url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            scratch = build_session_factory(engine)
            world = build_world(scratch, label="balance-backfill")
            provider = FakeProvider()
            fund_available(scratch, world, provider, 60_000)
            paid_payment(scratch, world, provider, amount=30_000)
            with engine.connect() as connection:
                maintained = _balance_rows(connection)
            assert len(maintained) >= len(MERCHANT_CODES)
            assert any(row[2] > 0 for row in maintained.values())

            command.downgrade(config, "0034_pending_action")
            command.upgrade(config, "0035_ledger_account_balances")
            with engine.connect() as connection:
                assert _balance_rows(connection) == maintained
                assert (
                    connection.scalar(text("SELECT count(*) FROM relaypay_ledger_balance_drift()"))
                    == 0
                )

            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE ledger_account_balances DISABLE TRIGGER {GUARD}")
                )
                connection.execute(
                    text(
                        "UPDATE ledger_account_balances SET credit_total = credit_total + 1 "
                        "WHERE account_id = (SELECT account_id FROM ledger_account_balances "
                        "ORDER BY posting_count DESC LIMIT 1)"
                    )
                )
                connection.execute(
                    text(f"ALTER TABLE ledger_account_balances ENABLE TRIGGER {GUARD}")
                )
            with (
                pytest.raises(DBAPIError, match="backfill disagrees with postings for 1 accounts"),
                engine.begin() as connection,
            ):
                connection.execute(text(migration.VERIFY_BACKFILL))
            with engine.connect() as connection:
                [drift] = connection.execute(
                    text("SELECT * FROM relaypay_ledger_balance_drift()")
                ).all()
                assert drift.stored_credit_total == drift.ledger_credit_total + 1
        finally:
            engine.dispose()
    finally:
        _drop_schema(base_url, schema)


@contextmanager
def _logging_preserved() -> Iterator[None]:
    """Alembic's env.py runs ``fileConfig``, which disables every existing logger; undo that so
    later tests that assert on log output still see it."""
    manager = logging.Logger.manager
    loggers = [logging.getLogger(name) for name in list(manager.loggerDict)]
    saved = [
        (logger, logger.disabled, logger.level, list(logger.handlers), logger.propagate)
        for logger in [logging.getLogger(), *loggers]
    ]
    try:
        yield
    finally:
        for logger, disabled, level, handlers, propagate in saved:
            logger.disabled = disabled
            logger.setLevel(level)
            logger.handlers[:] = handlers
            logger.propagate = propagate


def _migration_0035() -> ModuleType:
    path = MIGRATIONS / "0035_ledger_account_balances.py"
    spec = importlib.util.spec_from_file_location("migration_0035", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _balance_rows(connection: object) -> dict[uuid.UUID, tuple[int, int, int, int]]:
    rows = connection.execute(  # type: ignore[attr-defined]
        text(
            "SELECT account_id, credit_total, debit_total, posting_count, "
            "credit_total - debit_total FROM ledger_account_balances"
        )
    )
    return {row[0]: (row[1], row[2], row[3], row[4]) for row in rows}
