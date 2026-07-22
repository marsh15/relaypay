"""Prove v0.2 upgrades to M2 without rewriting existing evidence."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import make_url

from scripts.verify_m1_upgrade import _seed_v01_fixture

ROOT = Path(__file__).resolve().parents[1]
RELAYPAY_CONFIG_PATH = ROOT / "migrations" / "relaypay" / "alembic.ini"
PROVIDER_CONFIG_PATH = ROOT / "migrations" / "provider" / "alembic.ini"
NEW_RELAYPAY_TABLES = (
    "statement_imports",
    "statement_items",
    "reconciliation_runs",
    "reconciliation_matches",
    "reconciliation_mismatches",
    "mismatch_evidence_versions",
    "mismatch_workflow_history",
)
NEW_PROVIDER_TABLES = ("provider_statement_exports", "provider_statement_items")


def _base_url(config_path: Path, environment_name: str) -> str:
    config = Config(str(config_path))
    configured = config.get_main_option("sqlalchemy.url")
    if configured is None:
        raise RuntimeError(f"Migration URL is missing from {config_path}")
    return os.getenv(environment_name, configured)


def _schema_url(base_url: str, schema: str) -> str:
    url = make_url(base_url).update_query_dict({"options": f"-csearch_path={schema}"})
    return url.render_as_string(hide_password=False)


def _configuration(config_path: Path, database_url: str) -> Config:
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _create_schema(base_url: str, schema: str) -> None:
    engine = create_engine(base_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        engine.dispose()


def _drop_schema(base_url: str, schema: str) -> None:
    engine = create_engine(base_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        engine.dispose()


def _table_digests(connection: Connection) -> dict[str, str]:
    table_names = sorted(
        table for table in inspect(connection).get_table_names() if table != "alembic_version"
    )
    digests: dict[str, str] = {}
    preparer = connection.dialect.identifier_preparer
    for table in table_names:
        quoted = preparer.quote(table)
        rows = connection.execute(
            text(
                f"SELECT to_jsonb(t)::text FROM {quoted} t ORDER BY to_jsonb(t)::text"  # noqa: S608 - reflected table identifier is quoted
            )
        ).scalars()
        digests[table] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return digests


def _seed_provider_v02(connection: Connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO provider_accounts
              (id, public_id, name, signing_secret_digest)
            VALUES
              ('10000000-0000-0000-0000-000000000001', 'acct_m2_upgrade_fixture',
               'M2 upgrade fixture', decode(repeat('11', 32), 'hex'));
            INSERT INTO provider_effects
              (id, provider_account_id, stable_key, operation_kind, reference, amount,
               currency, request_sha256, outcome, response_bytes, completed_at)
            VALUES
              ('10000000-0000-0000-0000-000000000002',
               '10000000-0000-0000-0000-000000000001', 'capture:m2-upgrade', 'CAPTURE',
               'cap_m2_upgrade', 100, 'INR', decode(repeat('22', 32), 'hex'), 'SUCCEEDED',
               convert_to('{"outcome":"SUCCEEDED"}', 'UTF8'),
               '2026-07-01 00:00:00+00');
            """
        )
    )


def _verify_empty(connection: Connection, tables: tuple[str, ...]) -> None:
    for table in tables:
        quoted = connection.dialect.identifier_preparer.quote(table)
        count = connection.scalar(
            text(f"SELECT count(*) FROM {quoted}")  # noqa: S608 - fixed release table allowlist
        )
        assert count == 0, f"{table} was not empty after the v0.2 upgrade"


def main() -> None:
    schema = f"m2_upgrade_{uuid.uuid4().hex}"
    relaypay_base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    provider_base_url = _base_url(PROVIDER_CONFIG_PATH, "PROVIDER_MIGRATION_DATABASE_URL")
    _create_schema(relaypay_base_url, schema)
    _create_schema(provider_base_url, schema)
    original_relaypay_url = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    original_provider_url = os.environ.get("PROVIDER_MIGRATION_DATABASE_URL")
    try:
        relaypay_url = _schema_url(relaypay_base_url, schema)
        provider_url = _schema_url(provider_base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = relaypay_url
        os.environ["PROVIDER_MIGRATION_DATABASE_URL"] = provider_url
        relaypay_config = _configuration(RELAYPAY_CONFIG_PATH, relaypay_url)
        provider_config = _configuration(PROVIDER_CONFIG_PATH, provider_url)

        command.upgrade(relaypay_config, "0005_scenarios")
        relaypay_engine = create_engine(relaypay_url)
        with relaypay_engine.begin() as connection:
            _seed_v01_fixture(connection)
        command.upgrade(relaypay_config, "0006_identity_environments")
        with relaypay_engine.connect() as connection:
            relaypay_before = _table_digests(connection)
        command.upgrade(relaypay_config, "head")
        with relaypay_engine.connect() as connection:
            assert all(
                _table_digests(connection)[table] == digest
                for table, digest in relaypay_before.items()
            )
            _verify_empty(connection, NEW_RELAYPAY_TABLES)
        relaypay_engine.dispose()

        command.upgrade(provider_config, "0001_provider")
        provider_engine = create_engine(provider_url)
        with provider_engine.begin() as connection:
            _seed_provider_v02(connection)
        with provider_engine.connect() as connection:
            provider_before = _table_digests(connection)
        command.upgrade(provider_config, "head")
        with provider_engine.connect() as connection:
            assert all(
                _table_digests(connection)[table] == digest
                for table, digest in provider_before.items()
            )
            _verify_empty(connection, NEW_PROVIDER_TABLES)
        provider_engine.dispose()
    finally:
        if original_relaypay_url is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original_relaypay_url
        if original_provider_url is None:
            os.environ.pop("PROVIDER_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["PROVIDER_MIGRATION_DATABASE_URL"] = original_provider_url
        _drop_schema(relaypay_base_url, schema)
        _drop_schema(provider_base_url, schema)
    print("M2 upgrade proof passed: v0.2 evidence unchanged; reconciliation tables empty")


if __name__ == "__main__":
    main()
