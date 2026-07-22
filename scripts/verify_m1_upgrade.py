"""Prove the v0.1 schema upgrades to M1 without mutating evidence."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "migrations" / "relaypay" / "alembic.ini"
ENVIRONMENT_TABLES = (
    "customers",
    "payment_intents",
    "ledger_accounts",
    "provider_operations",
    "provider_attempts",
    "operation_history",
    "idempotency_records",
    "authorizations",
    "captures",
    "refunds",
    "journals",
    "postings",
    "webhook_endpoints",
    "webhook_endpoint_versions",
    "merchant_events",
    "event_recipients",
    "webhook_deliveries",
    "webhook_delivery_attempts",
    "scenario_runs",
)
IMMUTABLE_TABLES = (
    "provider_attempts",
    "operation_history",
    "journals",
    "postings",
    "webhook_endpoint_versions",
    "merchant_events",
    "event_recipients",
    "webhook_delivery_attempts",
)
ORG_ID = "00000000-0000-0000-0000-000000000001"


def _configuration() -> tuple[Config, str]:
    config = Config(str(CONFIG_PATH))
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url is None:
        raise RuntimeError("RelayPay migration database URL is not configured")
    database_url = os.getenv("RELAYPAY_MIGRATION_DATABASE_URL", configured_url)
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config, database_url


def _seed_v01_fixture(connection: Connection) -> None:
    fixture_sql = """
            INSERT INTO organisations (id, public_id, name, status)
            VALUES (:org_id, 'org_upgrade_fixture', 'M1 upgrade fixture', 'ACTIVE');
            INSERT INTO users
              (id, organisation_id, email_normalized, display_name, password_hash, role, status)
            VALUES
              ('00000000-0000-0000-0000-000000000002', :org_id,
               'upgrade-fixture@example.test', 'Upgrade fixture', 'synthetic-hash',
               'ADMIN', 'ACTIVE');
            INSERT INTO api_keys
              (id, organisation_id, name, public_prefix, secret_digest, scopes, status)
            VALUES
              ('00000000-0000-0000-0000-000000000003', :org_id, 'Upgrade key',
               'rpk_test_upgrade', decode(repeat('11', 32), 'hex'),
               ARRAY['payments:read'], 'ACTIVE');
            INSERT INTO customers
              (id, public_id, organisation_id, merchant_customer_reference, display_name)
            VALUES
              ('00000000-0000-0000-0000-000000000004', 'cus_upgrade_fixture', :org_id,
               'upgrade-customer', 'Upgrade customer');
            INSERT INTO payment_intents
              (id, public_id, organisation_id, customer_id, merchant_reference, amount, currency)
            VALUES
              ('00000000-0000-0000-0000-000000000005', 'pay_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000004', 'upgrade-payment', 100, 'INR');
            INSERT INTO ledger_accounts
              (id, organisation_id, code, name, account_type, currency)
            VALUES
              ('00000000-0000-0000-0000-000000000006', :org_id,
               'CASH', 'Cash', 'ASSET', 'INR'),
              ('00000000-0000-0000-0000-000000000007', :org_id,
               'MERCHANT', 'Merchant payable', 'LIABILITY', 'INR');
            INSERT INTO provider_operations
              (id, public_id, organisation_id, payment_intent_id, resource_type, resource_id,
               kind, stable_provider_key, status, attempt_count, apply_failure_count)
            VALUES
              ('00000000-0000-0000-0000-000000000008', 'pop_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000005', 'AUTHORIZATION',
               '00000000-0000-0000-0000-000000000009', 'AUTHORIZE',
               'upgrade-provider-key', 'PROCESSING', 1, 0);
            INSERT INTO authorizations
              (id, public_id, organisation_id, payment_intent_id, provider_operation_id,
               amount, currency, status)
            VALUES
              ('00000000-0000-0000-0000-000000000009', 'auth_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000005',
               '00000000-0000-0000-0000-000000000008', 100, 'INR', 'PROCESSING');
            INSERT INTO provider_attempts
              (id, organisation_id, provider_operation_id, sequence, attempt_kind, state,
               request_sha256, response_http_status, response_bytes, response_sha256,
               provider_signature_valid, classification, started_at, completed_at)
            VALUES
              ('00000000-0000-0000-0000-000000000010', :org_id,
               '00000000-0000-0000-0000-000000000008', 1, 'MUTATION',
               'RESPONSE_RECEIVED', decode(repeat('22', 32), 'hex'), 200,
               convert_to('{"status":"ok"}', 'UTF8'), decode(repeat('33', 32), 'hex'),
               true, 'SUCCESS', '2026-01-01 00:00:00+00', '2026-01-01 00:00:01+00');
            INSERT INTO operation_history
              (id, organisation_id, provider_operation_id, from_status, to_status,
               reason_code, evidence_attempt_id, actor_type, correlation_id)
            VALUES
              ('00000000-0000-0000-0000-000000000011', :org_id,
               '00000000-0000-0000-0000-000000000008', NULL, 'PROCESSING',
               'REQUEST_ACCEPTED', '00000000-0000-0000-0000-000000000010',
               'REQUEST', 'upgrade-correlation');
            INSERT INTO idempotency_records
              (id, organisation_id, key_digest, fingerprint_sha256, fingerprint_summary,
               target_type, target_id, provider_operation_id, is_terminal)
            VALUES
              ('00000000-0000-0000-0000-000000000012', :org_id,
               decode(repeat('44', 32), 'hex'), decode(repeat('55', 32), 'hex'), '{}',
               'PROVIDER_OPERATION', '00000000-0000-0000-0000-000000000008',
               '00000000-0000-0000-0000-000000000008', false);
            INSERT INTO journals
              (id, public_id, organisation_id, provider_operation_id, journal_type,
               reference_type, reference_id, currency)
            VALUES
              ('00000000-0000-0000-0000-000000000013', 'jnl_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000008', 'COMPENSATION', 'PAYMENT_INTENT',
               '00000000-0000-0000-0000-000000000005', 'INR');
            INSERT INTO postings
              (id, organisation_id, journal_id, account_id, side, amount, currency)
            VALUES
              ('00000000-0000-0000-0000-000000000014', :org_id,
               '00000000-0000-0000-0000-000000000013',
               '00000000-0000-0000-0000-000000000006', 'DEBIT', 100, 'INR'),
              ('00000000-0000-0000-0000-000000000015', :org_id,
               '00000000-0000-0000-0000-000000000013',
               '00000000-0000-0000-0000-000000000007', 'CREDIT', 100, 'INR');
            INSERT INTO merchant_events
              (id, public_id, organisation_id, payment_intent_id, provider_operation_id,
               event_type, schema_version, event_bytes, event_sha256, occurred_at)
            VALUES
              ('00000000-0000-0000-0000-000000000016', 'evt_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000005',
               '00000000-0000-0000-0000-000000000008', 'payment.authorized.v1', 1,
               convert_to('{"event":"upgrade"}', 'UTF8'), decode(repeat('66', 32), 'hex'),
               '2026-01-01 00:00:02+00');
            INSERT INTO webhook_endpoints (id, public_id, organisation_id, name, status)
            VALUES ('00000000-0000-0000-0000-000000000017', 'wep_upgrade_fixture',
                    :org_id, 'Upgrade endpoint', 'ACTIVE');
            INSERT INTO webhook_endpoint_versions
              (id, public_id, organisation_id, webhook_endpoint_id, version, url,
               encrypted_secret, subscribed_event_types, active_from)
            VALUES
              ('00000000-0000-0000-0000-000000000018', 'wev_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000017', 1,
               'https://example.test/webhook', decode(repeat('77', 32), 'hex'),
               ARRAY['payment.authorized.v1'], '2026-01-01 00:00:00+00');
            INSERT INTO event_recipients
              (id, organisation_id, merchant_event_id, endpoint_version_id)
            VALUES
              ('00000000-0000-0000-0000-000000000019', :org_id,
               '00000000-0000-0000-0000-000000000016',
               '00000000-0000-0000-0000-000000000018');
            INSERT INTO webhook_deliveries
              (id, public_id, organisation_id, event_recipient_id, status, attempt_count,
               next_attempt_at, lease_token, lease_expires_at)
            VALUES
              ('00000000-0000-0000-0000-000000000020', 'whd_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000019', 'DELIVERING', 1,
               '2026-01-01 00:00:03+00', '00000000-0000-0000-0000-000000000021',
               '2026-01-01 00:05:00+00');
            INSERT INTO webhook_delivery_attempts
              (id, organisation_id, webhook_delivery_id, sequence, lease_token,
               request_timestamp, event_sha256, response_http_status, result,
               started_at, completed_at)
            VALUES
              ('00000000-0000-0000-0000-000000000022', :org_id,
               '00000000-0000-0000-0000-000000000020', 1,
               '00000000-0000-0000-0000-000000000021', 1767225600,
               decode(repeat('66', 32), 'hex'), 200, 'ACKNOWLEDGED',
               '2026-01-01 00:00:03+00', '2026-01-01 00:00:04+00');
            INSERT INTO scenario_runs
              (id, public_id, organisation_id, payment_intent_id, scenario_type, status,
               correlation_id, steps, assertions, started_at, completed_at)
            VALUES
              ('00000000-0000-0000-0000-000000000023', 'scn_upgrade_fixture', :org_id,
               '00000000-0000-0000-0000-000000000005', 'LOST_CAPTURE_RESPONSE',
               'SUCCEEDED', 'upgrade-scenario', '[]', jsonb_build_object('passed', true),
               '2026-01-01 00:00:00+00', '2026-01-01 00:00:05+00');
            """
    for statement in fixture_sql.split(";"):
        if statement.strip():
            connection.execute(text(statement), {"org_id": ORG_ID})


def _evidence_digests(connection: Connection) -> dict[str, str]:
    digests: dict[str, str] = {}
    for table in IMMUTABLE_TABLES:
        rows = connection.execute(
            text(
                f"SELECT (to_jsonb(t) - 'environment_id')::text FROM {table} t ORDER BY id"  # noqa: S608 - fixed table allowlist
            )
        ).scalars()
        digests[table] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return digests


def _verify_backfill(connection: Connection, before: dict[str, str]) -> None:
    environments = connection.execute(
        text(
            "SELECT environment_type, id FROM environments "
            "WHERE organisation_id = :org_id ORDER BY environment_type"
        ),
        {"org_id": ORG_ID},
    ).all()
    assert [row.environment_type for row in environments] == ["LIVE_LIKE", "TEST"]
    test_environment_id = next(row.id for row in environments if row.environment_type == "TEST")
    for table in ENVIRONMENT_TABLES:
        count_query = (
            f"SELECT count(*) AS total, "  # noqa: S608 - fixed table allowlist
            f"count(*) FILTER (WHERE environment_id = :test_id) AS test_rows "
            f"FROM {table} WHERE organisation_id = :org_id"
        )
        counts = connection.execute(
            text(count_query),
            {"org_id": ORG_ID, "test_id": test_environment_id},
        ).one()
        assert counts.test_rows == counts.total, f"{table} did not backfill entirely to TEST"
    assert _evidence_digests(connection) == before
    membership_role = connection.scalar(
        text(
            "SELECT role FROM organisation_memberships "
            "WHERE organisation_id = :org_id AND user_id = "
            "'00000000-0000-0000-0000-000000000002'"
        ),
        {"org_id": ORG_ID},
    )
    assert membership_role == "ORGANISATION_ADMIN"
    key_version = connection.execute(
        text(
            "SELECT v.version, v.status, v.secret_digest = decode(repeat('11', 32), 'hex') "
            "AS digest_unchanged FROM api_key_versions v "
            "WHERE v.api_key_id = '00000000-0000-0000-0000-000000000003'"
        )
    ).one()
    assert tuple(key_version) == (1, "ACTIVE", True)


def main() -> None:
    config, database_url = _configuration()
    command.upgrade(config, "0005_scenarios")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _seed_v01_fixture(connection)
        before = _evidence_digests(connection)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        _verify_backfill(connection, before)
    engine.dispose()
    print("M1 upgrade proof passed: v0.1 rows -> TEST; LIVE_LIKE empty; evidence unchanged")


if __name__ == "__main__":
    main()
