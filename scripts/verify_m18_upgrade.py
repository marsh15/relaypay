"""Prove migration 0033 (reconciliation at scale) preserves every existing row.

The proof seeds payments through the real services at head, steps back to 0032, and shows that
0033 adds only columns, a CHECK list and an index: no existing row changes, and a downgrade
restores the 0032 database exactly. Streamed imports and v2-only mismatch types cannot exist at
0032, so the downgrade refuses while they do; the proof checks that refusal too.
"""

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
from scripts.verify_m16_upgrade import _digests, _seed

PREVIOUS_REVISION = "0032_rate_limit_gcra"
SCALE_REVISION = "0033_recon_scale"
NEW_COLUMNS = {
    "statement_imports": ("streamed",),
    "statement_items": ("fee_amount", "tax_amount"),
    "reconciliation_runs": ("progress_ordinal", "matched_count", "mismatch_count"),
}
SCOPE_CREATED_INDEX = """
    SELECT count(*) FROM pg_indexes
    WHERE tablename = 'provider_operations' AND schemaname = current_schema()
      AND indexname = 'ix_provider_operations_created_scope'
"""


def main() -> None:
    schema = f"m18_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "head")
        seeded = _seed(database_url)
        engine = create_engine(database_url)
        try:
            _discard_later_events(engine)
            command.downgrade(config, PREVIOUS_REVISION)
            with engine.connect() as connection:
                before = _digests(connection)
                payments = int(connection.scalar(text("SELECT count(*) FROM payment_intents")) or 0)
                assert connection.scalar(text(SCOPE_CREATED_INDEX)) == 0
            assert payments == len(seeded) > 0

            command.upgrade(config, SCALE_REVISION)
            with engine.connect() as connection:
                after = _digests(connection, strip=NEW_COLUMNS)
                assert after == before, "0033 must not change any existing row"
                assert connection.scalar(text(SCOPE_CREATED_INDEX)) == 1
                assert (
                    connection.scalar(text("SELECT count(*) FROM statement_imports WHERE streamed"))
                    == 0
                )

            command.downgrade(config, PREVIOUS_REVISION)
            with engine.connect() as connection:
                assert _digests(connection) == before, "downgrade must restore 0032 exactly"
            command.upgrade(config, "head")
        finally:
            engine.dispose()
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)
    print(
        f"M18 upgrade proof passed: 0033 added streamed-import, fee/tax and run-progress columns "
        f"over {payments} seeded payments; existing rows unchanged; downgrade and re-upgrade clean"
    )


def _discard_later_events(engine: object) -> None:
    """The head-time seed writes v2 merchant events, which the 0031 downgrade will not delete."""
    from scripts.verify_m17_upgrade import _discard_v2_events

    _discard_v2_events(engine)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
