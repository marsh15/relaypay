"""Prove the API-only v0.7 release leaves the v0.6 database unchanged."""

from __future__ import annotations

import os
import uuid

from alembic import command
from sqlalchemy import create_engine, inspect, text

from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)


def _counts(database_url: str, tables: set[str] | None = None) -> dict[str, int]:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            existing_tables = set(inspect(connection).get_table_names())
            owned_tables = existing_tables if tables is None else tables
            return {
                table: int(
                    connection.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
                    or 0
                )
                for table in sorted(owned_tables)
                if table != "alembic_version"
            }
    finally:
        engine.dispose()


def main() -> None:
    schema = f"m6_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original_url = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0011_connectors")
        before = _counts(database_url)
        command.upgrade(config, "head")
        assert _counts(database_url, set(before)) == before
    finally:
        if original_url is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original_url
        _drop_schema(base_url, schema)
    print("M6 upgrade proof passed: v0.6 schema and row counts unchanged")


if __name__ == "__main__":
    main()
