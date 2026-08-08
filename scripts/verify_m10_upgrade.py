"""Prove v0.10 upgrades to v0.11 without rewriting payment or evidence rows."""

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

AGENT_TABLES = {
    "approval_decisions",
    "approval_requests",
    "business_event_outbox",
    "consumed_business_events",
    "evaluation_datasets",
    "evaluation_runs",
    "model_invocations",
    "pricing_versions",
    "prompt_versions",
    "tool_invocations",
    "workflow_artifacts",
    "workflow_dead_letters",
    "workflow_definitions",
    "workflow_runs",
    "workflow_steps",
}


def _counts(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            return {
                table: int(connection.scalar(text(f'SELECT count(*) FROM "{table}"')) or 0)  # noqa: S608
                for table in inspect(connection).get_table_names()
                if table not in AGENT_TABLES | {"alembic_version"}
            }
    finally:
        engine.dispose()


def main() -> None:
    schema = f"m10_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)
        command.upgrade(config, "0012_observability")
        before = _counts(database_url)
        command.upgrade(config, "0013_agent_runtime")
        assert _counts(database_url) == before
        engine = create_engine(database_url)
        with engine.begin() as connection:
            for table in AGENT_TABLES:
                assert connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0  # noqa: S608
            role_constraint = connection.scalar(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'organisation_memberships_role_check'"
                )
            )
            assert role_constraint is not None and "APPROVER" in role_constraint
        engine.dispose()
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)
    print("M10 upgrade proof passed: v0.10 evidence preserved; v0.11 runtime starts empty")


if __name__ == "__main__":
    main()
