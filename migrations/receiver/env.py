from __future__ import annotations

import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from relaypay.receiver import models as _models  # noqa: F401
from relaypay.receiver.database import ReceiverBase
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("RECEIVER_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = ReceiverBase.metadata


def include_receiver_schema(
    object_: object,
    _name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object | None,
) -> bool:
    if type_ == "table":
        return getattr(object_, "schema", None) == "receiver"
    table = getattr(object_, "table", None)
    if table is not None:
        return getattr(table, "schema", None) == "receiver"
    return True


def configure_context(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
        include_object=include_receiver_schema,
        version_table_schema="receiver",
        **kwargs,
    )


def run_migrations_offline() -> None:
    configure_context(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        configure_context(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
