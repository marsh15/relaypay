import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.identity.models import (
    AuditRecord,
    Environment,
    Organisation,
    OrganisationMembership,
    User,
)
from relaypay.identity.security import Principal, hash_password
from relaypay.ids import new_public_id
from relaypay.reconciliation.models import ReconciliationRun, StatementImport, StatementItem
from relaypay.reconciliation.service import import_statement, parse_statement
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"


class ImportArguments(TypedDict):
    principal: Principal
    environment_public_id: str
    provider: str
    source_reference: str
    source_format: str
    period_start: datetime
    period_end: datetime
    raw_bytes: bytes


def _seed_identity() -> tuple[Engine, Principal, Environment]:
    engine = build_engine(DATABASE_URL, application_name="m2-reconciliation-test")
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="M2 reconciliation test", status="ACTIVE"
        )
        user = User(
            email_normalized=f"m2-{uuid.uuid4().hex}@example.test",
            display_name="M2 admin",
            password_hash=hash_password("Synthetic-M2-Admin-Password!"),
            platform_role="STANDARD",
            status="ACTIVE",
        )
        session.add_all([organisation, user])
        session.flush()
        session.add(
            OrganisationMembership(
                organisation_id=organisation.id,
                user_id=user.id,
                role="ORGANISATION_ADMIN",
                status="ACTIVE",
            )
        )
        environment = session.scalar(
            select(Environment).where(
                Environment.organisation_id == organisation.id,
                Environment.environment_type == "TEST",
            )
        )
        assert environment is not None
        principal = Principal(
            kind="SESSION",
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=None,
            environment_public_id=None,
            display_name=user.display_name,
            scopes=frozenset(),
            membership_role="ORGANISATION_ADMIN",
            user_id=user.id,
        )
    return engine, principal, environment


def _statement(occurred_at: datetime, *, amount: int = 100_000) -> bytes:
    return json.dumps(
        {
            "items": [
                {
                    "amount": amount,
                    "currency": "INR",
                    "occurredAt": occurred_at.isoformat(),
                    "operationKind": "CAPTURE",
                    "providerItemId": f"provider_{uuid.uuid4().hex}",
                    "stableKey": f"capture:pay_{uuid.uuid4().hex}",
                    "status": "SUCCEEDED",
                }
            ]
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_statement_import_replay_conflict_and_immutable_evidence() -> None:
    engine, principal, environment = _seed_identity()
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    raw_bytes = _statement(now)
    source_reference = f"provider_daily_{uuid.uuid4().hex}"
    arguments = ImportArguments(
        principal=principal,
        environment_public_id=environment.public_id,
        provider="PAYMENT_PROVIDER",
        source_reference=source_reference,
        source_format="JSON",
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
        raw_bytes=raw_bytes,
    )
    with factory() as session, session.begin():
        first = import_statement(session, **arguments)
        assert first.created
        assert first.reconciliation_run.status == "PENDING"
        first_import_id = first.statement_import.id
        first_run_id = first.reconciliation_run.id

    with factory() as session, session.begin():
        replay = import_statement(session, **arguments)
        assert not replay.created
        assert replay.statement_import.id == first_import_id
        assert replay.reconciliation_run.id == first_run_id

    with factory() as session, session.begin():
        conflict_arguments = arguments.copy()
        conflict_arguments["raw_bytes"] = _statement(now, amount=1)
        with pytest.raises(RelayPayError) as conflict:
            import_statement(session, **conflict_arguments)
        assert conflict.value.code == "STATEMENT_SOURCE_CONFLICT"
        assert conflict.value.http_status == 409

    with factory() as session, session.begin():
        assert (session.scalar(select(func.count()).select_from(StatementImport)) or 0) >= 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatementItem)
                .where(StatementItem.statement_import_id == first_import_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReconciliationRun)
                .where(ReconciliationRun.statement_import_id == first_import_id)
            )
            == 1
        )
        audit = session.scalar(
            select(AuditRecord).where(
                AuditRecord.action == "STATEMENT_IMPORTED",
                AuditRecord.target_id == replay.statement_import.public_id,
            )
        )
        assert audit is not None
        assert audit.details["sha256"] == hashlib.sha256(raw_bytes).hexdigest()

    with pytest.raises(DBAPIError), factory() as session, session.begin():
        session.execute(
            text("UPDATE statement_items SET amount = amount + 1 WHERE statement_import_id = :id"),
            {"id": first_import_id},
        )
    engine.dispose()


def test_statement_parser_accepts_csv_and_rejects_duplicate_items() -> None:
    occurred_at = datetime.now(UTC)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "providerItemId",
            "stableKey",
            "operationKind",
            "amount",
            "currency",
            "status",
            "occurredAt",
        ],
    )
    writer.writeheader()
    writer.writerow(
        {
            "providerItemId": "provider_item_1",
            "stableKey": "capture:payment_1",
            "operationKind": "CAPTURE",
            "amount": "100000",
            "currency": "INR",
            "status": "SUCCEEDED",
            "occurredAt": occurred_at.isoformat(),
        }
    )
    parsed = parse_statement(output.getvalue().encode(), "CSV")
    assert len(parsed) == 1
    assert parsed[0].amount == 100_000

    duplicated = json.dumps(
        {
            "items": [
                {
                    "providerItemId": "duplicate",
                    "stableKey": f"capture:{index}",
                    "operationKind": "CAPTURE",
                    "amount": 1,
                    "currency": "INR",
                    "status": "SUCCEEDED",
                    "occurredAt": occurred_at.isoformat(),
                }
                for index in range(2)
            ]
        }
    ).encode()
    with pytest.raises(RelayPayError) as error:
        parse_statement(duplicated, "JSON")
    assert error.value.code == "INVALID_STATEMENT"


def test_statement_import_requires_organisation_admin() -> None:
    engine, principal, environment = _seed_identity()
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    viewer = Principal(
        kind="SESSION",
        organisation_id=principal.organisation_id,
        organisation_public_id=principal.organisation_public_id,
        environment_id=None,
        environment_public_id=None,
        display_name="M2 viewer",
        scopes=frozenset(),
        membership_role="VIEWER",
        user_id=principal.user_id,
    )
    with factory() as session, session.begin():
        with pytest.raises(RelayPayError) as denied:
            import_statement(
                session,
                principal=viewer,
                environment_public_id=environment.public_id,
                provider="PAYMENT_PROVIDER",
                source_reference=f"denied_{uuid.uuid4().hex}",
                source_format="JSON",
                period_start=now - timedelta(hours=1),
                period_end=now + timedelta(hours=1),
                raw_bytes=_statement(now),
            )
        assert denied.value.http_status == 403
    engine.dispose()
