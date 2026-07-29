"""Prove v0.3 upgrades to M3 without rewriting historical financial evidence."""

from __future__ import annotations

import os
import uuid

from alembic import command
from sqlalchemy import Connection, create_engine, text

from scripts.verify_m1_upgrade import ORG_ID, _seed_v01_fixture
from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)

OLD_JOURNAL_ID = "00000000-0000-0000-0000-000000000013"
OLD_EVENT_ID = "00000000-0000-0000-0000-000000000016"


def _historical_evidence(connection: Connection) -> tuple[tuple[object, ...], list[str], bytes]:
    journal = connection.execute(
        text(
            """
            SELECT public_id, organisation_id, environment_id, provider_operation_id,
                   journal_type, reference_type, reference_id, currency, posted_at
              FROM journals WHERE id = :journal_id
            """
        ),
        {"journal_id": OLD_JOURNAL_ID},
    ).one()
    postings = list(
        connection.execute(
            text(
                """
                SELECT to_jsonb(p)::text FROM postings p
                 WHERE journal_id = :journal_id ORDER BY id
                """
            ),
            {"journal_id": OLD_JOURNAL_ID},
        ).scalars()
    )
    event_bytes = connection.scalar(
        text("SELECT event_bytes FROM merchant_events WHERE id = :event_id"),
        {"event_id": OLD_EVENT_ID},
    )
    assert event_bytes is not None
    return tuple(journal), postings, bytes(event_bytes)


def _verify_m3(connection: Connection, before: tuple[tuple[object, ...], list[str], bytes]) -> None:
    assert _historical_evidence(connection) == before
    test_environment_id = connection.scalar(
        text(
            """
            SELECT id FROM environments
             WHERE organisation_id = :org_id AND environment_type = 'TEST'
            """
        ),
        {"org_id": ORG_ID},
    )
    assert test_environment_id is not None
    default_merchant = connection.execute(
        text(
            """
            SELECT id, public_id FROM merchant_accounts
             WHERE organisation_id = :org_id AND environment_id = :environment_id
               AND is_default AND status = 'ACTIVE'
            """
        ),
        {"org_id": ORG_ID, "environment_id": test_environment_id},
    ).one()
    payment_merchant_id = connection.scalar(
        text(
            """
            SELECT merchant_account_id FROM payment_intents
             WHERE id = '00000000-0000-0000-0000-000000000005'
            """
        )
    )
    assert payment_merchant_id == default_merchant.id
    assert (
        connection.scalar(
            text(
                """
                SELECT count(*) FROM ledger_accounts
                 WHERE merchant_account_id = :merchant_id
                """
            ),
            {"merchant_id": default_merchant.id},
        )
        == 4
    )
    opening = connection.execute(
        text(
            """
            SELECT j.id, sum(CASE WHEN p.side = 'DEBIT' THEN p.amount ELSE 0 END) debit,
                   sum(CASE WHEN p.side = 'CREDIT' THEN p.amount ELSE 0 END) credit
              FROM journals j JOIN postings p ON p.journal_id = j.id
             WHERE j.merchant_account_id = :merchant_id AND j.journal_type = 'OPENING'
             GROUP BY j.id
            """
        ),
        {"merchant_id": default_merchant.id},
    ).one()
    assert (opening.debit, opening.credit) == (100, 100)
    opening_projection = connection.execute(
        text(
            """
            SELECT pending_delta, available_delta, receivable_delta
              FROM balance_transactions WHERE journal_id = :journal_id
            """
        ),
        {"journal_id": opening.id},
    ).one()
    assert tuple(opening_projection) == (100, 0, 0)

    new_org_id = uuid.uuid4()
    new_environment_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO organisations (id, public_id, name, status)
            VALUES (:org_id, :public_id, 'M3 trigger fixture', 'ACTIVE')
            """
        ),
        {"org_id": new_org_id, "public_id": f"org_{new_org_id.hex}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO environments
              (id, public_id, organisation_id, name, environment_type, status)
            VALUES
              (:environment_id, :environment_public_id, :org_id, 'Test', 'TEST', 'ACTIVE')
            """
        ),
        {
            "org_id": new_org_id,
            "environment_id": new_environment_id,
            "environment_public_id": f"env_{new_environment_id.hex}",
        },
    )
    trigger_result = connection.execute(
        text(
            """
            SELECT count(*) merchants,
                   (SELECT count(*) FROM ledger_accounts a
                     WHERE a.environment_id = :environment_id
                       AND a.merchant_account_id IS NOT NULL) templates
              FROM merchant_accounts m
             WHERE m.environment_id = :environment_id AND m.is_default
            """
        ),
        {"environment_id": new_environment_id},
    ).one()
    assert tuple(trigger_result) == (1, 4)


def main() -> None:
    schema = f"m3_upgrade_{uuid.uuid4().hex}"
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
            before = _historical_evidence(connection)
        command.upgrade(config, "head")
        with engine.begin() as connection:
            _verify_m3(connection, before)
        engine.dispose()
    finally:
        if original_url is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original_url
        _drop_schema(base_url, schema)
    print(
        "M3 upgrade proof passed: v0.3 evidence unchanged; one opening transfer; "
        "default merchants provisioned"
    )


if __name__ == "__main__":
    main()
