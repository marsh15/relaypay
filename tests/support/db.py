"""Database helpers shared by integration, proof and chaos tests.

URLs match the constants hard-coded across ``tests/integration`` (Compose PostgreSQL on
localhost:55432). The ``RELAYPAY_DATABASE_URL`` / ``PROVIDER_DATABASE_URL`` environment
variables override them, which is how CI or a remote database is selected.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from relaypay.database import build_engine, build_session_factory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_RELAYPAY_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
DEFAULT_PROVIDER_URL = "postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider"


def relaypay_database_url() -> str:
    return os.environ.get("RELAYPAY_DATABASE_URL", DEFAULT_RELAYPAY_URL)


def provider_database_url() -> str:
    return os.environ.get("PROVIDER_DATABASE_URL", DEFAULT_PROVIDER_URL)


def build_test_engine(
    url: str,
    *,
    application_name: str,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> Engine:
    """Build an engine like ``relaypay.database.build_engine``, optionally with a larger pool.

    The default SQLAlchemy pool (5 + 10 overflow per engine) is the limit for thread-based
    contention tests; pass ``pool_size``/``max_overflow`` to raise it. Mind the server-side
    ``max_connections`` (100 in the stock Compose Postgres).
    """
    if pool_size is None and max_overflow is None:
        return build_engine(url, application_name=application_name)
    from relaypay.observability.telemetry import instrument_sqlalchemy_engine

    engine = create_engine(
        url,
        connect_args={"application_name": application_name},
        pool_pre_ping=True,
        pool_size=pool_size if pool_size is not None else 5,
        max_overflow=max_overflow if max_overflow is not None else 10,
    )
    instrument_sqlalchemy_engine(engine)
    return engine


def relaypay_engine(
    application_name: str = "relaypay-tests",
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> Engine:
    return build_test_engine(
        relaypay_database_url(),
        application_name=application_name,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


def provider_engine(
    application_name: str = "relaypay-provider-tests",
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> Engine:
    return build_test_engine(
        provider_database_url(),
        application_name=application_name,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


@contextmanager
def relaypay_factory(
    application_name: str = "relaypay-tests",
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> Iterator[sessionmaker[Session]]:
    """Yield a RelayPay session factory (autobegin off, defaults installed); dispose on exit."""
    engine = relaypay_engine(application_name, pool_size=pool_size, max_overflow=max_overflow)
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


@contextmanager
def provider_factory(
    application_name: str = "relaypay-provider-tests",
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
) -> Iterator[sessionmaker[Session]]:
    """Yield a session factory bound to the provider database; dispose on exit."""
    engine = provider_engine(application_name, pool_size=pool_size, max_overflow=max_overflow)
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


def run_in_transaction[T](factory: sessionmaker[Session], work: Callable[[Session], T]) -> T:
    """Run ``work`` in a fresh session inside one committed transaction and return its result."""
    with factory() as session, session.begin():
        return work(session)


def require_postgres(url: str | None = None) -> None:
    """Skip the calling test or module if PostgreSQL is unreachable.

    Existing integration tests fail hard when the database is down; proof tests that are
    optional locally call this first so a missing Compose stack reads as a skip.
    """
    engine = build_test_engine(
        url or relaypay_database_url(), application_name="relaypay-tests-probe"
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as error:
        pytest.skip(f"PostgreSQL is unreachable: {type(error).__name__}")
    finally:
        engine.dispose()
