"""Prove v0.5 upgrades to M5 without rewriting financial or integration evidence."""

from __future__ import annotations

import os
import uuid

from alembic import command
from sqlalchemy import create_engine, text

from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)

M5_TABLES = (
    "connectors",
    "connector_versions",
    "connector_credential_versions",
    "connector_health_observations",
    "inbound_webhook_events",
    "inbound_webhook_attempts",
)
PRESERVED_TABLES = (
    "organisations",
    "environments",
    "payment_intents",
    "provider_operations",
    "journals",
    "postings",
    "merchant_events",
    "payouts",
    "payout_attempt_evidence",
)


def main() -> None:
    schema = f"m5_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original_url = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0010_payouts")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            before = {
                table: int(
                    connection.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
                    or 0
                )
                for table in PRESERVED_TABLES
            }
        command.upgrade(config, "head")
        with engine.begin() as connection:
            after = {
                table: int(
                    connection.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
                    or 0
                )
                for table in PRESERVED_TABLES
            }
            assert after == before
            assert all(
                connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0  # noqa: S608
                for table in M5_TABLES
            )
        engine.dispose()
    finally:
        if original_url is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original_url
        _drop_schema(base_url, schema)
    print("M5 upgrade proof passed: v0.5 evidence unchanged; connector tables start empty")


if __name__ == "__main__":
    main()
