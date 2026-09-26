"""Prove v0.14 upgrades to v0.15 without rewriting authoritative rows."""

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

RISK_TABLES = {
    "onboarding_snapshots",
    "risk_annotations",
    "risk_escalations",
    "risk_findings",
    "risk_reviews",
    "risk_review_versions",
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
                if table not in RISK_TABLES | {"alembic_version"}
            }
    finally:
        engine.dispose()


def main() -> None:
    schema = f"m14_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0016_settlement_intelligence")
        before = _counts(database_url)
        command.upgrade(config, "0017_merchant_risk_review")
        assert _counts(database_url) == before
        engine = create_engine(database_url)
        with engine.begin() as connection:
            for table in RISK_TABLES:
                assert connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0  # noqa: S608
        engine.dispose()
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)
    print("M14 upgrade proof passed: v0.14 evidence preserved; v0.15 risk review starts empty")


if __name__ == "__main__":
    main()
