from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class RelayPaySession(Session):
    def flush(self, objects: Sequence[Any] | None = None) -> None:
        # Partial flushes can strand legacy tenant rows deferred until TEST exists.
        super().flush()


def build_engine(database_url: str, *, application_name: str) -> Engine:
    return create_engine(
        database_url,
        connect_args={"application_name": application_name},
        pool_pre_ping=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    from relaypay.identity.environments import install_environment_defaults

    install_environment_defaults()
    return sessionmaker(
        bind=engine, class_=RelayPaySession, expire_on_commit=False, autobegin=False
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Open a synchronous session; callers explicitly demarcate transactions."""

    with factory() as session:
        yield session
