"""Prove migration 0019 (payment state machine) backfills correctly and rewrites no evidence.

Runs in a throwaway schema. Later M16 tasks extend this script with their own migrations
(0020 onward); this revision covers 0019 only.

1. Upgrade to 0019 and drive real payments through the real services (only the provider is
   faked) into every lifecycle state, recording the status the state machine itself produced.
2. Downgrade to 0018. The schema and data are now exactly what a pre-0019 database holds: no
   ``status`` column, no history table, no transition trigger.
3. Record a ``to_jsonb`` checksum of every table, confirm ``payment_intents`` has no
   immutability trigger, and upgrade to 0019 again.
4. Assert the backfilled statuses equal both the expected lifecycle state and the status the
   state machine produced, one ``BACKFILL_V2`` history row per payment, ``status_version = 0``.
5. Assert every table (journals, postings, merchant_events, operation_history,
   provider_attempts, captures, refunds and the rest) has an unchanged checksum, with the sole
   exception of ``payment_intents``, which is compared with its two new columns removed.
6. Assert the trigger rejects an illegal pair with SQLSTATE RP001 and accepts a legal one, then
   downgrade to 0018 (every checksum back to the baseline, new objects gone) and upgrade again.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import uuid
from dataclasses import dataclass
from types import ModuleType

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    ROOT,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)

PREVIOUS_REVISION = "0018_immutability_backfill"
REVISION = "0019_payment_state_machine"
GUARD_REVISION = "0020_payout_overdraft_guard"
EXPIRY_REVISION = "0023_authorization_expiry"
GUARD_TRIGGERS = {
    ("payout_reservation_history", "payout_reservations_covered_at_commit"),
    ("postings", "available_debits_covered_at_commit"),
}
MIGRATION_PATH = ROOT / "migrations" / "relaypay" / "versions" / "0019_payment_state_machine.py"
NEW_COLUMNS = ("status", "status_version")
NEW_FUNCTION = "relaypay_enforce_payment_status_transition"
NEW_TRIGGER = "payment_intents_status_transition"
HISTORY_TRIGGER = "payment_status_history_immutable"
NEW_INDEX = "ix_payment_intents_scope_status_created_id"


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0019", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(database_url: str) -> dict[str, str]:
    """Drive payments into every lifecycle state at 0019; return label -> expected status.

    The caller reads the real statuses back afterwards, so the expectations here are checked
    against both the intended scenario and the state machine's own output.
    """
    from relaypay.database import build_session_factory

    from tests.support.fake_provider import FakeProvider, Outcome
    from tests.support.world import (
        World,
        authorize,
        build_world,
        capture,
        create_payment,
        dispatch,
        refund,
    )

    engine = create_engine(database_url)
    factory: sessionmaker[Session] = build_session_factory(engine)
    expected: dict[str, str] = {}
    try:
        world: World = build_world(factory, label="m16-upgrade")
        provider = FakeProvider()

        def payment(status: str, amount: int = 10_000) -> str:
            payment_id = create_payment(factory, world, amount=amount)
            expected[payment_id] = status
            return payment_id

        # created: nothing started, and an authorization still in flight (lost response).
        payment("created")
        lost = payment("created")
        auth_op = authorize(factory, world, lost)
        provider.default = Outcome.LOSE_RESPONSE
        dispatch(factory, world, auth_op, provider)
        provider.default = Outcome.SUCCEED

        # failed: authorization declined.
        declined = payment("failed")
        provider.default = Outcome.DECLINE
        authorize(factory, world, declined, provider)
        provider.default = Outcome.SUCCEED

        # authorised, and failed (capture declined).
        authorised = payment("authorised")
        authorize(factory, world, authorised, provider)
        capture_declined = payment("failed")
        authorize(factory, world, capture_declined, provider)
        provider.default = Outcome.DECLINE
        capture(factory, world, capture_declined, provider)
        provider.default = Outcome.SUCCEED

        def captured(status: str, amount: int = 10_000) -> str:
            payment_id = payment(status, amount)
            authorize(factory, world, payment_id, provider)
            capture(factory, world, payment_id, provider)
            return payment_id

        # captured, including one whose only refund failed and one whose refund is in flight.
        captured("captured")
        refund_failed = captured("captured")
        provider.default = Outcome.DECLINE
        refund(factory, world, refund_failed, 1_000, provider)
        provider.default = Outcome.SUCCEED
        refund_in_flight = captured("captured")
        refund_op = refund(factory, world, refund_in_flight, 1_000)
        provider.default = Outcome.LOSE_RESPONSE
        dispatch(factory, world, refund_op, provider)
        provider.default = Outcome.SUCCEED

        # partially_refunded: one refund, and one success after a declined refund.
        partial = captured("partially_refunded")
        refund(factory, world, partial, 3_000, provider)
        mixed = captured("partially_refunded")
        provider.default = Outcome.DECLINE
        refund(factory, world, mixed, 9_000, provider)
        provider.default = Outcome.SUCCEED
        refund(factory, world, mixed, 4_000, provider)

        # refunded: one full refund, and two refunds that sum to the capture amount.
        full = captured("refunded")
        refund(factory, world, full, 10_000, provider)
        split = captured("refunded")
        refund(factory, world, split, 6_000, provider)
        refund(factory, world, split, 4_000, provider)
    finally:
        engine.dispose()
    return expected


def _digests(
    connection: Connection, *, strip: dict[str, tuple[str, ...]] | None = None
) -> dict[str, str]:
    """Per-table sha256 over ``to_jsonb`` of every row; ``strip`` removes columns from a table."""
    strip = strip or {}
    preparer = connection.dialect.identifier_preparer
    digests: dict[str, str] = {}
    for table in sorted(inspect(connection).get_table_names()):
        if table == "alembic_version":
            continue
        projection = "to_jsonb(t)" + "".join(f" - '{column}'" for column in strip.get(table, ()))
        rows = connection.execute(
            text(
                f"SELECT ({projection})::text FROM {preparer.quote(table)} t "  # noqa: S608 - reflected, quoted
                f"ORDER BY ({projection})::text"
            )
        ).scalars()
        digests[table] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return digests


def _triggers(connection: Connection) -> dict[str, str]:
    rows = connection.execute(
        text(
            """
            SELECT t.tgname, c.relname
            FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal
            """
        )
    )
    return {name: table for name, table in rows}


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


def _index_exists(connection: Connection, name: str) -> bool:
    return bool(
        connection.scalar(
            text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE indexname = :name AND schemaname = current_schema()"
            ),
            {"name": name},
        )
    )


def _columns(connection: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _statuses(connection: Connection) -> dict[str, str]:
    rows = connection.execute(text("SELECT public_id, status FROM payment_intents"))
    return {public_id: status for public_id, status in rows}


@dataclass(frozen=True)
class Baseline:
    digests: dict[str, str]  # every table at 0018 (full rows)
    triggers: dict[str, str]
    payment_count: int


def _assert_not_upgraded(engine: Engine, baseline: Baseline) -> None:
    with engine.connect() as connection:
        assert _digests(connection) == baseline.digests, "0018 checksums changed"
        assert not set(NEW_COLUMNS) & _columns(connection, "payment_intents")
        assert "payment_status_history" not in inspect(connection).get_table_names()
        assert _triggers(connection) == baseline.triggers, "downgrade left a trigger behind"
        assert not _function_exists(connection, NEW_FUNCTION)
        assert not _index_exists(connection, NEW_INDEX)


def _assert_upgraded(
    engine: Engine, baseline: Baseline, expected: dict[str, str], recorded: dict[str, str]
) -> None:
    with engine.connect() as connection:
        # Evidence unchanged: every old table byte-identical, payment_intents minus new columns.
        digests = _digests(connection, strip={"payment_intents": NEW_COLUMNS})
        history_digest = digests.pop("payment_status_history")
        assert history_digest
        assert digests == baseline.digests, "evidence checksums changed across 0019"

        # Backfill correctness.
        statuses = _statuses(connection)
        assert len(statuses) == baseline.payment_count
        for public_id, status in expected.items():
            assert statuses[public_id] == status, (public_id, statuses[public_id], status)
            assert recorded[public_id] == status, "state machine disagrees with the backfill"
        assert set(statuses.values()) >= {
            "created",
            "authorised",
            "captured",
            "partially_refunded",
            "refunded",
            "failed",
        }
        assert (
            connection.scalar(
                text("SELECT count(*) FROM payment_intents WHERE status_version <> 0")
            )
            == 0
        )

        # One BACKFILL_V2 history row per payment, from NULL, version 0, ending at the status.
        history = connection.execute(
            text(
                "SELECT p.public_id, h.from_status, h.to_status, h.reason, h.actor, "
                "h.status_version FROM payment_status_history h "
                "JOIN payment_intents p ON p.id = h.payment_intent_id"
            )
        ).all()
        assert len(history) == baseline.payment_count
        for public_id, from_status, to_status, reason, actor, version in history:
            assert from_status is None and reason == "BACKFILL_V2" and version == 0
            assert actor == "MIGRATION" and to_status == statuses[public_id]

        # Guards exist and behave.
        triggers = _triggers(connection)
        assert triggers.pop(NEW_TRIGGER) == "payment_intents"
        assert triggers.pop(HISTORY_TRIGGER) == "payment_status_history"
        assert triggers == baseline.triggers
        assert _function_exists(connection, NEW_FUNCTION)
        assert _index_exists(connection, NEW_INDEX)
        _assert_trigger_behaviour(connection, statuses)


def _assert_trigger_behaviour(connection: Connection, statuses: dict[str, str]) -> None:
    connection.rollback()  # end the read transaction autobegun above
    outer = connection.begin()
    try:
        authorised = next(pid for pid, status in statuses.items() if status == "authorised")
        savepoint = connection.begin_nested()
        try:
            connection.execute(
                text("UPDATE payment_intents SET status = 'refunded' WHERE public_id = :id"),
                {"id": authorised},
            )
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "RP001", error
        else:
            raise AssertionError("authorised -> refunded must be rejected")
        finally:
            savepoint.rollback()
        assert (
            connection.execute(
                text("UPDATE payment_intents SET status = 'captured' WHERE public_id = :id"),
                {"id": authorised},
            ).rowcount
            == 1
        )
    finally:
        outer.rollback()


def _guard_triggers(connection: Connection) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE t.tgname IN ('payout_reservations_covered_at_commit', "
            "'available_debits_covered_at_commit') AND t.tgdeferrable"
            " AND c.relnamespace = to_regnamespace(current_schema())"
        )
    ).all()
    return {(str(row[0]), str(row[1])) for row in rows}


def _prove_overdraft_guard(config: Config, database_url: str) -> None:
    """0020 adds only deferred constraint triggers: no row changes, clean down and up."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            before = _digests(connection)
            assert _guard_triggers(connection) == set()
        command.upgrade(config, GUARD_REVISION)
        with engine.connect() as connection:
            assert _guard_triggers(connection) == GUARD_TRIGGERS
            assert _digests(connection) == before, "0020 must not change any row"
        command.downgrade(config, REVISION)
        with engine.connect() as connection:
            assert _guard_triggers(connection) == set()
            assert _digests(connection) == before
        command.upgrade(config, GUARD_REVISION)
        with engine.connect() as connection:
            assert _guard_triggers(connection) == GUARD_TRIGGERS
    finally:
        engine.dispose()


def _prove_authorization_expiry(config: Config, database_url: str) -> None:
    """0023 backfills expires_at = authorized_at + 7 days for every succeeded authorization."""
    command.upgrade(config, EXPIRY_REVISION)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            succeeded = int(
                connection.scalar(
                    text("SELECT count(*) FROM authorizations WHERE status = 'SUCCEEDED'")
                )
                or 0
            )
            wrong = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM authorizations WHERE "
                        "hold_seconds <> 604800 OR "
                        "(status = 'SUCCEEDED') <> (expires_at IS NOT NULL) OR "
                        "(status = 'SUCCEEDED' AND "
                        "expires_at <> authorized_at + interval '7 days')"
                    )
                )
                or 0
            )
        assert succeeded > 0, "the seeded data must include succeeded authorizations"
        assert wrong == 0, f"{wrong} authorizations backfilled incorrectly"
        command.downgrade(config, GUARD_REVISION)
        command.upgrade(config, EXPIRY_REVISION)
    finally:
        engine.dispose()


def main() -> None:
    module = _migration_module()
    assert module.SQLSTATE == "RP001"
    schema = f"m16_upgrade_{uuid.uuid4().hex}"
    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    try:
        database_url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = database_url
        config = _configuration(RELAYPAY_CONFIG_PATH, database_url)

        # Seed through the real services at head (the services use today's models), then step
        # back so the data is exactly a 0018 database.
        command.upgrade(config, "head")
        expected = _seed(database_url)
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                recorded = _statuses(connection)
        finally:
            engine.dispose()
        command.downgrade(config, PREVIOUS_REVISION)

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                triggers = _triggers(connection)
                assert "payment_intents" not in triggers.values(), (
                    "payment_intents must have no immutability trigger"
                )
                baseline = Baseline(
                    digests=_digests(connection),
                    triggers=triggers,
                    payment_count=int(
                        connection.scalar(text("SELECT count(*) FROM payment_intents")) or 0
                    ),
                )
            assert baseline.payment_count == len(recorded) >= 12
            with engine.connect() as connection:
                for table in ("journals", "postings", "provider_attempts", "operation_history"):
                    assert (
                        connection.scalar(text(f"SELECT count(*) FROM {table}")) or 0  # noqa: S608
                    ) > 0, f"{table} must hold seeded evidence"
            engine.dispose()

            command.upgrade(config, REVISION)
            engine = create_engine(database_url)
            _assert_upgraded(engine, baseline, expected, recorded)
            engine.dispose()

            command.downgrade(config, PREVIOUS_REVISION)
            engine = create_engine(database_url)
            _assert_not_upgraded(engine, baseline)
            engine.dispose()

            command.upgrade(config, REVISION)
            engine = create_engine(database_url)
            _assert_upgraded(engine, baseline, expected, recorded)
            engine.dispose()
            _prove_overdraft_guard(config, database_url)
            _prove_authorization_expiry(config, database_url)
        finally:
            engine.dispose()
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)
    print(
        "M16 upgrade proof passed: 0019 backfilled "
        f"{baseline.payment_count} payments across every lifecycle state; "
        "evidence checksums unchanged; downgrade and re-upgrade clean; "
        "0020 overdraft guard triggers added without changing rows; "
        "0023 authorization expiry backfilled"
    )


if __name__ == "__main__":
    main()
