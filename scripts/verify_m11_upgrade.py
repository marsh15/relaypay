"""Prove v0.11 upgrades to v0.12 without rewriting authoritative rows."""

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

DISPUTE_TABLES = {
    "dispute_cases",
    "dispute_draft_versions",
    "dispute_package_versions",
    "dispute_submission_attempts",
}


def _counts(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            return {
                table: int(
                    connection.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0  # noqa: S608
                )
                for table in inspect(connection).get_table_names()
                if table not in DISPUTE_TABLES | {"alembic_version"}
            }
    finally:
        engine.dispose()


def main() -> None:
    schema = f"m11_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0013_agent_runtime")
        before = _counts(database_url)
        command.upgrade(config, "0014_disputes")
        assert _counts(database_url) == before
        engine = create_engine(database_url)
        with engine.begin() as connection:
            for table in DISPUTE_TABLES:
                assert connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0  # noqa: S608
        engine.dispose()
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)
    print("M11 upgrade proof passed: v0.11 evidence preserved; v0.12 disputes start empty")


if __name__ == "__main__":
    main()
