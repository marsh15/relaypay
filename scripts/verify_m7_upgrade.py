"""Prove v0.7 upgrades add only bounded operations storage and the slow mock fault."""

from __future__ import annotations

import os
import uuid

from alembic import command
from sqlalchemy import create_engine, inspect, text

from scripts.verify_m2_upgrade import (
    PROVIDER_CONFIG_PATH,
    RELAYPAY_CONFIG_PATH,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)


def _table_counts(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            return {
                table: int(
                    connection.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
                    or 0
                )
                for table in inspect(connection).get_table_names()
                if table not in {"alembic_version", "request_logs", "usage_rollups"}
            }
    finally:
        engine.dispose()


def _prove_relaypay() -> None:
    schema = f"m7_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0011_connectors")
        before = _table_counts(database_url)
        # Historical proofs stop at their release boundary; later milestones may add storage.
        command.upgrade(config, "0012_observability")
        assert _table_counts(database_url) == before
        engine = create_engine(database_url)
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM request_logs")) == 0
            assert connection.scalar(text("SELECT count(*) FROM usage_rollups")) == 0
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND indexname = 'ix_payment_intents_scope_created_id'"
                    )
                )
                == 1
            )
        engine.dispose()
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)


def _prove_provider() -> None:
    schema = f"m7_provider_{uuid.uuid4().hex}"
    base_url = _base_url(PROVIDER_CONFIG_PATH, "PROVIDER_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    try:
        database_url = _schema_url(base_url, schema)
        config = _configuration(PROVIDER_CONFIG_PATH, database_url)
        command.upgrade(config, "0002_provider_statements")
        before = _table_counts(database_url)
        command.upgrade(config, "head")
        assert _table_counts(database_url) == before
    finally:
        _drop_schema(base_url, schema)


def main() -> None:
    _prove_relaypay()
    _prove_provider()
    print("M7 upgrade proof passed: evidence preserved; operations tables start empty")


if __name__ == "__main__":
    main()
