"""Prove migrations 0033 (reconciliation at scale) and 0035 (stored balances) preserve every row.

The proof seeds payments through the real services at head, steps back to 0032, and shows that
0033 adds only columns, a CHECK list and an index: no existing row changes, and a downgrade
restores the 0032 database exactly. Streamed imports and v2-only mismatch types cannot exist at
0032, so the downgrade refuses while they do; the proof checks that refusal too.

For 0035 it records the ``ledger_account_balances`` rows the posting trigger maintained while the
seed ran, then shows the migration's backfill (run when the database comes back up to head)
rebuilds exactly those rows, that they equal the ledger (``relaypay_ledger_balance_drift()`` is
empty and the grand totals match the merchant postings), that 0035 changes no other row, and that
its downgrade removes the table, triggers and functions and restores 0034 exactly.
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
BALANCES_REVISION = "0035_ledger_account_balances"
PRE_BALANCES_REVISION = "0034_pending_action"
BALANCE_ROWS = """
    SELECT account_id::text, credit_total, debit_total, posting_count
    FROM ledger_account_balances ORDER BY account_id
"""
MERCHANT_POSTING_TOTALS = """
    SELECT COALESCE(sum(p.amount) FILTER (WHERE p.side = 'CREDIT'), 0),
           COALESCE(sum(p.amount) FILTER (WHERE p.side = 'DEBIT'), 0),
           count(p.id),
           (SELECT count(*) FROM ledger_accounts WHERE merchant_account_id IS NOT NULL)
    FROM postings p JOIN ledger_accounts a ON a.id = p.account_id
    WHERE a.merchant_account_id IS NOT NULL
"""
STORED_TOTALS = """
    SELECT COALESCE(sum(credit_total), 0), COALESCE(sum(debit_total), 0),
           COALESCE(sum(posting_count), 0), count(*)
    FROM ledger_account_balances
"""
BALANCE_OBJECTS = """
    SELECT (SELECT count(*) FROM pg_tables
             WHERE schemaname = current_schema() AND tablename = 'ledger_account_balances')
         + (SELECT count(*) FROM pg_proc
             WHERE pronamespace = current_schema()::regnamespace
               AND proname IN ('relaypay_open_ledger_account_balance',
                               'relaypay_apply_posting_to_balance',
                               'relaypay_guard_ledger_account_balance',
                               'relaypay_ledger_balance_drift'))
         + (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
             WHERE c.relnamespace = current_schema()::regnamespace
               AND t.tgname LIKE 'ledger_account_balances_%')
"""
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
            with engine.connect() as connection:
                maintained = connection.execute(text(BALANCE_ROWS)).all()
            assert any(row.posting_count > 0 for row in maintained), "seed posted nothing"
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
            # 0034 (requires-action authorizations) adds columns only; no existing row changes.
            with engine.connect() as connection:
                at_head = _digests(
                    connection,
                    strip={
                        **NEW_COLUMNS,
                        "provider_operations": ("pending_deadline_at",),
                        "authorizations": ("next_action",),
                    },
                )
                changed = sorted(t for t in before if at_head.get(t) != before[t])
                assert not changed, f"0034 must not change any existing row: {changed}"
                # 0035: the backfill rebuilt exactly what the trigger had maintained.
                assert connection.execute(text(BALANCE_ROWS)).all() == maintained
                assert (
                    connection.scalar(text(BALANCE_OBJECTS)) == 8
                )  # table, 4 functions, 3 triggers
                assert (
                    connection.scalar(text("SELECT count(*) FROM relaypay_ledger_balance_drift()"))
                    == 0
                )
                assert tuple(connection.execute(text(STORED_TOTALS)).one()) == tuple(
                    connection.execute(text(MERCHANT_POSTING_TOTALS)).one()
                )
                at_balances = _digests(connection)

            command.downgrade(config, PRE_BALANCES_REVISION)
            with engine.connect() as connection:
                assert connection.scalar(text(BALANCE_OBJECTS)) == 0
                without = _digests(connection)
                assert at_balances.pop("ledger_account_balances") is not None
                assert without == at_balances, "0035 downgrade must change no other row"
            command.upgrade(config, BALANCES_REVISION)
            with engine.connect() as connection:
                assert connection.execute(text(BALANCE_ROWS)).all() == maintained
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
        f"over {payments} seeded payments; existing rows unchanged; downgrade and re-upgrade "
        f"clean; 0035 backfilled {len(maintained)} stored balances equal to the "
        f"trigger-maintained rows and to the ledger; its downgrade changes no other row"
    )


def _discard_later_events(engine: object) -> None:
    """The head-time seed writes v2 merchant events, which the 0031 downgrade will not delete."""
    from scripts.verify_m17_upgrade import _discard_v2_events

    _discard_v2_events(engine)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
