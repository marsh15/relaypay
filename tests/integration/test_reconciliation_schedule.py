"""M18 T18.2: the daily automatic statement import."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from relaypay.identity.models import AuditRecord
from relaypay.provider_operations.models import ProviderOperation
from relaypay.reconciliation import schedule
from relaypay.reconciliation.models import ReconciliationRun, StatementImport, StatementItem
from relaypay.reconciliation.schedule import (
    json_statement_to_csv,
    previous_utc_day,
    run_daily_statement_import,
)
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import World, build_world, paid_payment

pytestmark = pytest.mark.integration


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("reconciliation-schedule-tests", pool_size=6) as session_factory:
        yield session_factory


def _world_with_yesterdays_payment(
    factory: sessionmaker[Session], label: str, yesterday: datetime
) -> World:
    world = build_world(factory, label=label)
    paid_payment(factory, world, FakeProvider())
    with factory() as session, session.begin():
        session.execute(
            update(ProviderOperation)
            .where(ProviderOperation.organisation_id == world.organisation_id)
            .values(created_at=yesterday)
        )
    return world


def _statement_for(factory: sessionmaker[Session], world: World, when: datetime) -> bytes:
    with factory() as session, session.begin():
        operations = list(
            session.scalars(
                select(ProviderOperation).where(
                    ProviderOperation.organisation_id == world.organisation_id
                )
            )
        )
        items = [
            {
                "providerItemId": f"prov_{operation.public_id}",
                "stableKey": operation.stable_provider_key,
                "operationKind": operation.kind,
                "amount": 100_000,
                "currency": "INR",
                "status": "SUCCEEDED",
                "occurredAt": when.isoformat(),
            }
            for operation in operations
        ]
    return json.dumps({"items": items}).encode()


def test_previous_utc_day_is_the_whole_day_before_now() -> None:
    day, start, end = previous_utc_day(datetime(2026, 11, 19, 0, 5, tzinfo=UTC))
    assert day.isoformat() == "2026-11-18"
    assert (start.isoformat(), end.isoformat()) == (
        "2026-11-18T00:00:00+00:00",
        "2026-11-19T00:00:00+00:00",
    )


def test_json_export_becomes_the_streamed_csv() -> None:
    raw = json.dumps(
        {
            "items": [
                {
                    "providerItemId": "p1",
                    "stableKey": "capture:pay_1",
                    "operationKind": "CAPTURE",
                    "amount": 5,
                    "currency": "INR",
                    "status": "SUCCEEDED",
                    "occurredAt": "2026-11-18T10:00:00+00:00",
                }
            ]
        }
    ).encode()
    lines = json_statement_to_csv(raw).decode().splitlines()
    assert lines[0] == "providerItemId,stableKey,operationKind,amount,currency,status,occurredAt"
    assert lines[1] == "p1,capture:pay_1,CAPTURE,5,INR,SUCCEEDED,2026-11-18T10:00:00+00:00"


def test_daily_import_runs_once_per_day_and_audits_as_system(
    factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    day, start, _ = previous_utc_day(now)
    yesterday = start + timedelta(hours=12)
    world = _world_with_yesterdays_payment(factory, "schedule-once", yesterday)
    statement = _statement_for(factory, world, yesterday)
    calls: list[str] = []

    def fetch(reference: str, period_start: datetime, period_end: datetime) -> bytes:
        calls.append(reference)
        assert (period_start, period_end - period_start) == (start, timedelta(days=1))
        return statement

    first = run_daily_statement_import(factory, fetch=fetch, now=now)
    second = run_daily_statement_import(factory, fetch=fetch, now=now)

    assert first >= 1 and second == 0
    assert calls == [f"auto:{day.isoformat()}"]  # the second tick did not even ask the provider
    with factory() as session, session.begin():
        statement_import = session.scalar(
            select(StatementImport).where(StatementImport.organisation_id == world.organisation_id)
        )
        assert statement_import is not None
        assert statement_import.source_reference == f"auto:{day.isoformat()}"
        assert statement_import.streamed
        run = session.scalar(
            select(ReconciliationRun).where(
                ReconciliationRun.statement_import_id == statement_import.id
            )
        )
        assert run is not None and run.algorithm_version == 2 and run.status == "PENDING"
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatementItem)
                .where(StatementItem.statement_import_id == statement_import.id)
            )
            == 2
        )
        audit = session.scalar(
            select(AuditRecord).where(
                AuditRecord.organisation_id == world.organisation_id,
                AuditRecord.action == "STATEMENT_IMPORTED",
            )
        )
        assert audit is not None
        assert (audit.actor_type, audit.actor_id) == ("SYSTEM", None)


def test_an_environment_with_no_activity_yesterday_is_not_imported(
    factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    _, start, _ = previous_utc_day(now)
    quiet = build_world(factory, label="schedule-quiet")  # no operations at all
    active = _world_with_yesterdays_payment(factory, "schedule-active", start + timedelta(hours=3))
    statement = _statement_for(factory, active, start + timedelta(hours=3))

    run_daily_statement_import(factory, fetch=lambda *_: statement, now=now)

    with factory() as session, session.begin():
        imported = set(
            session.scalars(
                select(StatementImport.organisation_id).where(
                    StatementImport.organisation_id.in_(
                        [quiet.organisation_id, active.organisation_id]
                    )
                )
            )
        )
    assert imported == {active.organisation_id}


def test_a_failing_environment_does_not_stop_the_others(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    _, start, _ = previous_utc_day(now)
    when = start + timedelta(hours=6)
    broken = _world_with_yesterdays_payment(factory, "schedule-broken", when)
    healthy = _world_with_yesterdays_payment(factory, "schedule-healthy", when)
    statement = _statement_for(factory, healthy, when)
    real = schedule.import_streamed_statement_as_system

    def flaky(session: Session, **kwargs: Any) -> Any:
        if kwargs["environment_id"] == broken.environment_id:
            raise RuntimeError("simulated failure for one environment")
        return real(session, **kwargs)

    monkeypatch.setattr(schedule, "import_streamed_statement_as_system", flaky)

    created = run_daily_statement_import(factory, fetch=lambda *_: statement, now=now)

    assert created >= 1
    with factory() as session, session.begin():
        imported = set(
            session.scalars(
                select(StatementImport.organisation_id).where(
                    StatementImport.organisation_id.in_(
                        [broken.organisation_id, healthy.organisation_id]
                    )
                )
            )
        )
    assert imported == {healthy.organisation_id}
