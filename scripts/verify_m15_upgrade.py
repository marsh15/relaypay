"""Prove migration 0018 adds immutability triggers without rewriting any existing row.

Runs in a throwaway schema: upgrade to 0017, seed representative rows for the tables that
0018 protects (and a few it leaves mutable), upgrade to 0018, then check that every table keeps
its row count and ``to_jsonb`` checksum, that exactly the expected triggers exist, that they
reject mutation (SQLSTATE 55000) while the lifecycle columns still change, and that downgrade
removes exactly those triggers (the 0001 function stays) and re-upgrade restores them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType

from alembic import command
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    ROOT,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
    _table_digests,
)

PREVIOUS_REVISION = "0017_merchant_risk_review"
REVISION = "0018_immutability_backfill"
MIGRATION_PATH = ROOT / "migrations" / "relaypay" / "versions" / "0018_immutability_backfill.py"
EXPECTED_FULL_TABLES = 17
EXPECTED_COLUMN_SCOPED_TABLES = 7
FULL_FUNCTION = "relaypay_prevent_evidence_mutation"
COLUMN_FUNCTION = "relaypay_prevent_frozen_column_mutation"
# Class C tables seeded to prove they stay writable.
MUTABLE_SEEDED = ("workflow_runs", "workflow_steps", "approval_requests", "risk_reviews")


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0018", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def _seed(database_url: str) -> dict[str, int]:
    """Insert rows (at schema 0017) through the ORM; returns the expected row count per table."""
    from relaypay.database import build_session_factory
    from relaypay.identity.models import Environment as EnvironmentModel
    from relaypay.merchant_balances.models import MerchantAccount
    from relaypay.models import (
        ApprovalRequest,
        BusinessEventOutbox,
        ModelInvocation,
        OnboardingSnapshot,
        Organisation,
        PricingVersion,
        RiskFinding,
        RiskReview,
        RiskReviewVersion,
        SettlementPolicy,
        WorkflowArtifact,
        WorkflowDefinition,
        WorkflowRun,
        WorkflowStep,
    )
    from sqlalchemy import select

    engine = create_engine(database_url)
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex
    try:
        with factory() as session, session.begin():
            organisation = Organisation(public_id=f"org_{tag}", name=f"m15 {tag}", status="ACTIVE")
            session.add(organisation)
            session.flush()
            environment = session.scalar(
                select(EnvironmentModel).where(
                    EnvironmentModel.organisation_id == organisation.id,
                    EnvironmentModel.environment_type == "TEST",
                )
            )
            assert environment is not None
            merchant = session.scalar(
                select(MerchantAccount).where(MerchantAccount.environment_id == environment.id)
            )
            assert merchant is not None
            scope = {"organisation_id": organisation.id, "environment_id": environment.id}

            def add(row: object) -> uuid.UUID:
                session.add(row)
                session.flush()
                return row.id  # type: ignore[attr-defined, no-any-return]

            add(
                PricingVersion(
                    public_id=f"prv_{tag}",
                    provider="fixture",
                    model_id=f"model-{tag}",
                    version=1,
                    input_usd_micros_per_million=1,
                    output_usd_micros_per_million=2,
                )
            )
            definition_id = add(
                WorkflowDefinition(
                    **scope,
                    public_id=f"wdf_{tag}",
                    name=f"workflow-{tag}",
                    version=1,
                    definition_sha256=_sha("definition"),
                    definition={},
                    status="ACTIVE",
                )
            )
            run_id = add(
                WorkflowRun(
                    **scope,
                    public_id=f"wfr_{tag}",
                    workflow_definition_id=definition_id,
                    route="route",
                    idempotency_digest=_sha("idempotency"),
                    status="RUNNING",
                    token_budget=1000,
                    cost_budget_usd_micros=1000,
                )
            )
            step_id = add(
                WorkflowStep(
                    **scope,
                    public_id=f"wfs_{tag}",
                    workflow_run_id=run_id,
                    step_key="step",
                    step_kind="SYSTEM",
                    definition={},
                    status="RUNNING",
                    max_attempts=3,
                    next_attempt_at=now,
                )
            )
            artifact_id = add(
                WorkflowArtifact(
                    **scope,
                    public_id=f"wfa_{tag}",
                    workflow_run_id=run_id,
                    artifact_type="fixture",
                    version=1,
                    media_type="application/json",
                    content_bytes=b"{}",
                    content_sha256=_sha("{}"),
                    byte_length=2,
                )
            )
            add(
                ApprovalRequest(
                    **scope,
                    public_id=f"apr_{tag}",
                    workflow_run_id=run_id,
                    artifact_id=artifact_id,
                    artifact_sha256=_sha("{}"),
                    maker_user_id=uuid.uuid4(),
                    status="PENDING",
                )
            )
            add(
                ModelInvocation(
                    **scope,
                    public_id=f"mdi_{tag}",
                    workflow_run_id=run_id,
                    workflow_step_id=step_id,
                    provider="fixture",
                    model_id="fixture-model",
                    prompt_version="pmt:1",
                    pricing_version="prv:1",
                    request_sha256=_sha("req"),
                    response_sha256=_sha("res"),
                    latency_ms=1,
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd_micros=1,
                    finish_status="STOP",
                    trace_id=uuid.uuid4().hex,
                )
            )
            add(
                BusinessEventOutbox(
                    **scope,
                    event_id=f"evt_{tag}",
                    event_type="fixture.v1",
                    schema_version=1,
                    occurred_at=now,
                    resource_type="fixture",
                    resource_id=tag,
                    topic="fixture",
                    partition_key=tag,
                    payload={},
                    payload_sha256=_sha("payload"),
                    event_bytes=b"{}",
                    next_attempt_at=now,
                )
            )
            add(
                SettlementPolicy(
                    **scope,
                    public_id=f"spv_{tag}",
                    merchant_account_id=merchant.id,
                    version=1,
                    timezone_name="Asia/Kolkata",
                    cutoff_hour=17,
                    cutoff_minute=0,
                    settlement_delay_days=1,
                    weekend_handling="SKIP",
                    policy_sha256=_sha("policy"),
                    status="ACTIVE",
                )
            )
            snapshot_id = add(
                OnboardingSnapshot(
                    **scope,
                    public_id=f"obs_{tag}",
                    site_ref=f"site-{tag}",
                    snapshot={},
                    snapshot_sha256=_sha("snapshot"),
                    source_url="https://example.test",
                )
            )
            review_id = add(
                RiskReview(
                    **scope,
                    public_id=f"rrv_{tag}",
                    onboarding_snapshot_id=snapshot_id,
                    status="PENDING",
                    score_version=1,
                )
            )
            add(
                RiskReviewVersion(
                    **scope,
                    public_id=f"rvv_{tag}",
                    risk_review_id=review_id,
                    version=1,
                    score_version=1,
                    completeness_score=1,
                    domain_score=1,
                    category_score=1,
                    pricing_score=1,
                    claims_score=1,
                    total_score=5,
                    severity="LOW",
                    confidence="HIGH",
                    breakdown={},
                    review_sha256=_sha("review"),
                )
            )
            add(
                RiskFinding(
                    **scope,
                    public_id=f"rfd_{tag}",
                    risk_review_id=review_id,
                    position=1,
                    finding_type="DETERMINISTIC",
                    check_key="fixture",
                    classification="NONE",
                    severity="NONE",
                    confidence="HIGH",
                    detail="synthetic",
                    snapshot_sha256=_sha("snapshot"),
                )
            )
    finally:
        engine.dispose()
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return _counts(connection)
    finally:
        engine.dispose()


def _counts(connection: Connection) -> dict[str, int]:
    preparer = connection.dialect.identifier_preparer
    return {
        table: int(
            connection.scalar(
                text(f"SELECT count(*) FROM {preparer.quote(table)}")  # noqa: S608 - reflected, quoted
            )
            or 0
        )
        for table in sorted(inspect(connection).get_table_names())
        if table != "alembic_version"
    }


def _triggers(connection: Connection) -> dict[str, tuple[str, str]]:
    """Non-internal trigger name -> (table, function) in the current schema."""
    rows = connection.execute(
        text(
            """
            SELECT t.tgname, c.relname, p.proname
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal
            """
        )
    )
    return {name: (table, function) for name, table, function in rows}


def _function_exists(connection: Connection, name: str) -> bool:
    return bool(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_proc "
                "WHERE proname = :name AND pronamespace = current_schema()::regnamespace"
            ),
            {"name": name},
        )
    )


def _expected_triggers(module: ModuleType) -> dict[str, tuple[str, str]]:
    expected = {
        f"{table}_immutable": (table, FULL_FUNCTION) for table in module.FULL_IMMUTABLE_TABLES
    }
    expected.update(
        {
            f"{table}_frozen_columns": (table, COLUMN_FUNCTION)
            for table in module.COLUMN_SCOPED_TABLES
        }
    )
    return expected


def _assert_sqlstate_55000(connection: Connection, statement: str) -> None:
    transaction = connection.begin_nested()
    try:
        connection.execute(text(statement))
    except DBAPIError as error:
        assert getattr(error.orig, "sqlstate", None) == "55000", error
    else:
        raise AssertionError(f"expected SQLSTATE 55000 from: {statement}")
    finally:
        transaction.rollback()


def _assert_behaviour(connection: Connection) -> None:
    """Triggers reject mutation of protected rows and leave lifecycle and class C rows alone."""
    connection.rollback()  # end the autobegun read transaction
    outer = connection.begin()
    try:
        for table in ("workflow_artifacts", "model_invocations", "risk_findings"):
            _assert_sqlstate_55000(
                connection,
                f"UPDATE {table} SET created_at = created_at + interval '1 second'",  # noqa: S608
            )
            _assert_sqlstate_55000(connection, f"DELETE FROM {table}")  # noqa: S608
        _assert_sqlstate_55000(
            connection, "UPDATE workflow_definitions SET definition_sha256 = decode('00', 'hex')"
        )
        _assert_sqlstate_55000(connection, "DELETE FROM settlement_policies")
        assert (
            connection.execute(text("UPDATE workflow_definitions SET status = 'RETIRED'")).rowcount
            == 1
        )
        assert (
            connection.execute(text("UPDATE settlement_policies SET status = 'RETIRED'")).rowcount
            == 1
        )
        for table in MUTABLE_SEEDED:
            assert (
                connection.execute(
                    text(f"UPDATE {table} SET updated_at = now()")  # noqa: S608
                ).rowcount
                == 1
            )
        assert (
            connection.execute(
                text("UPDATE business_event_outbox SET publish_attempts = publish_attempts + 1")
            ).rowcount
            == 1
        )
    finally:
        outer.rollback()


def _digests(database_url: str) -> dict[str, str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return _table_digests(connection)
    finally:
        engine.dispose()


@dataclass(frozen=True)
class Baseline:
    triggers: set[str]
    digests: dict[str, str]
    counts: dict[str, int]


def _check_state(
    database_url: str, module: ModuleType, baseline: Baseline, *, protected: bool
) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert _counts(connection) == baseline.counts, "row counts changed"
            assert _table_digests(connection) == baseline.digests, "row checksums changed"
            triggers = _triggers(connection)
            expected = _expected_triggers(module)
            assert _function_exists(connection, FULL_FUNCTION), "0001 function must survive"
            if protected:
                assert set(triggers) == baseline.triggers | set(expected)
                for name, target in expected.items():
                    assert triggers[name] == target, (name, triggers[name], target)
                assert _function_exists(connection, COLUMN_FUNCTION)
                _assert_behaviour(connection)
            else:
                assert set(triggers) == baseline.triggers, "downgrade left a trigger behind"
                assert not _function_exists(connection, COLUMN_FUNCTION)
    finally:
        engine.dispose()


def main() -> None:
    module = _migration_module()
    assert len(module.FULL_IMMUTABLE_TABLES) == EXPECTED_FULL_TABLES
    assert len(module.COLUMN_SCOPED_TABLES) == EXPECTED_COLUMN_SCOPED_TABLES
    schema = f"m15_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)

        command.upgrade(config, PREVIOUS_REVISION)
        counts = _seed(database_url)
        assert counts["workflow_runs"] == 1 and counts["risk_findings"] == 1
        digests = _digests(database_url)
        engine = create_engine(database_url)
        with engine.connect() as connection:
            baseline_triggers = set(_triggers(connection))
            assert not baseline_triggers & set(_expected_triggers(module))
        engine.dispose()
        baseline = Baseline(baseline_triggers, digests, counts)

        command.upgrade(config, REVISION)
        _check_state(database_url, module, baseline, protected=True)
        command.downgrade(config, PREVIOUS_REVISION)
        _check_state(database_url, module, baseline, protected=False)
        command.upgrade(config, REVISION)
        _check_state(database_url, module, baseline, protected=True)
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)
    print(
        "M15 upgrade proof passed: 0018 added "
        f"{len(module.FULL_IMMUTABLE_TABLES)} full-row and "
        f"{len(module.COLUMN_SCOPED_TABLES)} column-scoped triggers; "
        "rows and checksums unchanged; downgrade and re-upgrade clean"
    )


if __name__ == "__main__":
    main()
