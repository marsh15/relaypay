"""Prove the M17 migrations preserve every existing row.

0026 adds the raw PSP webhook inbox. The proof seeds payments in every lifecycle state through the
real services at head (the services use today's models), steps back to 0025, and then shows that
0026 creates the empty ``psp_events`` table with its frozen-column trigger and changes no existing
row. 0027 lets webhooks be provider evidence (the WEBHOOK attempt kind and actor, and the unique
webhook-payload index) and must also change no row. A downgrade must restore the 0025 database
exactly, and the re-upgrade must be clean.
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

PREVIOUS_REVISION = "0025_fees_gst"
INBOX_REVISION = "0026_psp_inbox"
WEBHOOK_EVIDENCE_REVISION = "0027_webhook_evidence"
WEBHOOK_INDEX_COUNT = """
    SELECT count(*) FROM pg_indexes
    WHERE tablename = 'provider_attempts' AND schemaname = current_schema()
      AND indexname = 'uq_provider_attempts_one_webhook_payload'
"""
FROZEN_TRIGGER_COUNT = """
    SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE c.relname = 'psp_events' AND t.tgname = 'psp_events_frozen_columns'
      AND c.relnamespace = to_regnamespace(current_schema())
"""


def main() -> None:
    schema = f"m17_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "head")
        seeded = _seed(database_url)
        command.downgrade(config, PREVIOUS_REVISION)
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                before = _digests(connection)
                assert "psp_events" not in before
                payments = int(connection.scalar(text("SELECT count(*) FROM payment_intents")) or 0)
            assert payments == len(seeded) > 0

            command.upgrade(config, INBOX_REVISION)
            with engine.connect() as connection:
                after = _digests(connection)
                assert after.pop("psp_events", None) is not None, "0026 must create psp_events"
                assert after == before, "0026 must not change any existing row"
                assert connection.scalar(text("SELECT count(*) FROM psp_events")) == 0
                assert connection.scalar(text(FROZEN_TRIGGER_COUNT)) == 1

            command.upgrade(config, WEBHOOK_EVIDENCE_REVISION)
            with engine.connect() as connection:
                evidence = _digests(connection)
                evidence.pop("psp_events", None)
                assert evidence == before, "0027 must not change any existing row"
                assert connection.scalar(text(WEBHOOK_INDEX_COUNT)) == 1

            command.downgrade(config, PREVIOUS_REVISION)
            with engine.connect() as connection:
                assert _digests(connection) == before, "downgrade must restore 0025 exactly"
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
        f"M17 upgrade proof passed: 0026 added the PSP inbox and 0027 webhook evidence over "
        f"{payments} seeded payments; existing rows unchanged; downgrade and re-upgrade clean"
    )


if __name__ == "__main__":
    main()
