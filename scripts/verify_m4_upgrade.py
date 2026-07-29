"""Prove v0.4 upgrades to M4 without rewriting historical financial evidence."""

from __future__ import annotations

import os
import uuid

from alembic import command
from sqlalchemy import Connection, create_engine, text

from scripts.verify_m1_upgrade import _seed_v01_fixture
from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)
from scripts.verify_m3_upgrade import _historical_evidence

NEW_TABLES = (
    "beneficiaries",
    "payouts",
    "payout_reservation_history",
    "payout_attempts",
    "payout_attempt_evidence",
    "payout_history",
    "payout_events",
)


def _row_counts(connection: Connection) -> dict[str, int]:
    tables = (
        "organisations",
        "environments",
        "payment_intents",
        "journals",
        "postings",
        "merchant_events",
        "merchant_accounts",
        "balance_transactions",
    )
    return {
        table: int(
            connection.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
            or 0
        )
        for table in tables
    }


def main() -> None:
    schema = f"m4_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original_url = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0005_scenarios")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            _seed_v01_fixture(connection)
        command.upgrade(config, "0008_statement_currency")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE ledger_accounts SET code = 'PROVIDER_CLEARING_ASSET'
                     WHERE id = '00000000-0000-0000-0000-000000000006';
                    UPDATE ledger_accounts SET code = 'MERCHANT_PAYABLE_LIABILITY'
                     WHERE id = '00000000-0000-0000-0000-000000000007'
                    """
                )
            )
        command.upgrade(config, "0009_merchant_balances")
        with engine.begin() as connection:
            evidence_before = _historical_evidence(connection)
            counts_before = _row_counts(connection)
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert _historical_evidence(connection) == evidence_before
            assert _row_counts(connection) == counts_before
            assert all(
                connection.scalar(text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
                == 0
                for table in NEW_TABLES
            )
        engine.dispose()
    finally:
        if original_url is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original_url
        _drop_schema(base_url, schema)
    print(
        "M4 upgrade proof passed: v0.4 rows and immutable evidence unchanged; payout tables empty"
    )


if __name__ == "__main__":
    main()
