"""Prove the v0.9 console-only release does not rewrite v0.8 storage."""

from __future__ import annotations

import os
import uuid

from alembic import command

from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)
from scripts.verify_m6_upgrade import _counts


def main() -> None:
    schema = f"m8_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original_url = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0012_observability")
        before = _counts(database_url)
        command.upgrade(config, "head")
        assert _counts(database_url, set(before)) == before
    finally:
        if original_url is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original_url
        _drop_schema(base_url, schema)
    print("M8 upgrade proof passed: v0.8 schema and row counts unchanged")


if __name__ == "__main__":
    main()
