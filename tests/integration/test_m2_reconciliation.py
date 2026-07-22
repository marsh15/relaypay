import csv
import hashlib
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import (
    AuditRecord,
    Environment,
    Organisation,
    OrganisationMembership,
    User,
)
from relaypay.identity.security import Principal, hash_password
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import Authorization, Capture, Customer, PaymentIntent
from relaypay.provider_operations.models import ProviderOperation
from relaypay.reconciliation.models import (
    MismatchEvidenceVersion,
    MismatchWorkflowHistory,
    ReconciliationMatch,
    ReconciliationMismatch,
    ReconciliationRun,
    StatementImport,
    StatementItem,
)
from relaypay.reconciliation.service import (
    acknowledge_mismatch,
    claim_reconciliation_run,
    import_statement,
    parse_statement,
    process_reconciliation_claim,
    refresh_mismatch_evidence,
    resolve_mismatch,
)
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from apps.api.main import create_app

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


def test_concurrent_statement_source_replay_creates_one_import_and_run() -> None:
    engine, principal, environment = _seed_identity()
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    arguments = ImportArguments(
        principal=principal,
        environment_public_id=environment.public_id,
        provider="PAYMENT_PROVIDER",
        source_reference=f"concurrent-replay-{uuid.uuid4().hex}",
        source_format="JSON",
        period_start=now - timedelta(minutes=1),
        period_end=now + timedelta(minutes=1),
        raw_bytes=_statement(now),
    )

    def execute_import() -> tuple[bool, uuid.UUID, uuid.UUID]:
        with factory() as session, session.begin():
            result = import_statement(session, **arguments)
            return result.created, result.statement_import.id, result.reconciliation_run.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: execute_import(), range(2)))
    assert {created for created, _, _ in results} == {False, True}
    assert len({statement_id for _, statement_id, _ in results}) == 1
    assert len({run_id for _, _, run_id in results}) == 1
    engine.dispose()


def _add_authorization(
    session: Session,
    *,
    principal: Principal,
    environment: Environment,
    customer: Customer,
    stable_key: str,
    amount: int = 100_000,
) -> tuple[ProviderOperation, Authorization, PaymentIntent]:
    payment = PaymentIntent(
        public_id=new_public_id("pay"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        customer_id=customer.id,
        merchant_reference=f"m2-payment-{uuid.uuid4().hex}",
        amount=amount,
        currency="INR",
    )
    session.add(payment)
    session.flush([payment])
    operation_id = new_uuid()
    authorization_id = new_uuid()
    response = canonical_json_bytes({"status": "SUCCEEDED"})
    operation = ProviderOperation(
        id=operation_id,
        public_id=new_public_id("op"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        payment_intent_id=payment.id,
        resource_type="AUTHORIZATION",
        resource_id=authorization_id,
        kind="AUTHORIZE",
        stable_provider_key=stable_key,
        status="SUCCEEDED",
        attempt_count=1,
        apply_failure_count=0,
        provider_request_bytes=b"{}",
        provider_request_sha256=hashlib.sha256(b"{}").digest(),
        last_sent_at=datetime.now(UTC),
        terminal_http_status=200,
        terminal_response_headers={"Content-Type": "application/json"},
        terminal_response_bytes=response,
        terminal_response_sha256=hashlib.sha256(response).digest(),
        finalized_at=datetime.now(UTC),
    )
    authorization = Authorization(
        id=authorization_id,
        public_id=new_public_id("auth"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        payment_intent_id=payment.id,
        provider_operation_id=operation.id,
        amount=amount,
        currency="INR",
        status="SUCCEEDED",
        authorized_at=datetime.now(UTC),
    )
    session.add_all([operation, authorization])
    session.flush([operation, authorization])
    return operation, authorization, payment


def _statement_item(
    *,
    stable_key: str,
    occurred_at: datetime,
    operation_kind: str = "AUTHORIZE",
    amount: int = 100_000,
    currency: str = "INR",
    status: str = "SUCCEEDED",
) -> dict[str, object]:
    return {
        "providerItemId": f"provider_{uuid.uuid4().hex}",
        "stableKey": stable_key,
        "operationKind": operation_kind,
        "amount": amount,
        "currency": currency,
        "status": status,
        "occurredAt": occurred_at.isoformat(),
    }


def test_leased_reconciliation_is_deterministic_and_covers_seeded_mismatches() -> None:
    engine, principal, environment = _seed_identity()
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            merchant_customer_reference=f"m2-customer-{uuid.uuid4().hex}",
            display_name="Synthetic M2 customer",
        )
        session.add(customer)
        session.flush([customer])
        exact, exact_authorization, exact_payment = _add_authorization(
            session,
            principal=principal,
            environment=environment,
            customer=customer,
            stable_key=f"authorize:exact:{uuid.uuid4().hex}",
        )
        amount_mismatch, _, _ = _add_authorization(
            session,
            principal=principal,
            environment=environment,
            customer=customer,
            stable_key=f"authorize:amount:{uuid.uuid4().hex}",
        )
        currency_mismatch, _, _ = _add_authorization(
            session,
            principal=principal,
            environment=environment,
            customer=customer,
            stable_key=f"authorize:currency:{uuid.uuid4().hex}",
        )
        status_mismatch, _, _ = _add_authorization(
            session,
            principal=principal,
            environment=environment,
            customer=customer,
            stable_key=f"authorize:status:{uuid.uuid4().hex}",
        )
        duplicate, _, _ = _add_authorization(
            session,
            principal=principal,
            environment=environment,
            customer=customer,
            stable_key=f"authorize:duplicate:{uuid.uuid4().hex}",
        )
        _add_authorization(
            session,
            principal=principal,
            environment=environment,
            customer=customer,
            stable_key=f"authorize:missing-provider:{uuid.uuid4().hex}",
        )
        capture_id = new_uuid()
        capture_operation = ProviderOperation(
            id=new_uuid(),
            public_id=new_public_id("op"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=exact_payment.id,
            resource_type="CAPTURE",
            resource_id=capture_id,
            kind="CAPTURE",
            stable_provider_key=f"capture:missing-journal:{uuid.uuid4().hex}",
            status="PROCESSING",
            attempt_count=0,
            apply_failure_count=0,
        )
        capture = Capture(
            id=capture_id,
            public_id=new_public_id("cap"),
            organisation_id=principal.organisation_id,
            environment_id=environment.id,
            payment_intent_id=exact_payment.id,
            authorization_id=exact_authorization.id,
            provider_operation_id=capture_operation.id,
            amount=100_000,
            currency="INR",
            status="PROCESSING",
        )
        session.add_all([capture_operation, capture])

    items = [
        _statement_item(stable_key=exact.stable_provider_key, occurred_at=now),
        _statement_item(
            stable_key=amount_mismatch.stable_provider_key, occurred_at=now, amount=100_001
        ),
        _statement_item(
            stable_key=currency_mismatch.stable_provider_key, occurred_at=now, currency="USD"
        ),
        _statement_item(
            stable_key=status_mismatch.stable_provider_key, occurred_at=now, status="DECLINED"
        ),
        _statement_item(stable_key=duplicate.stable_provider_key, occurred_at=now),
        _statement_item(stable_key=duplicate.stable_provider_key, occurred_at=now),
        _statement_item(stable_key=f"authorize:absent:{uuid.uuid4().hex}", occurred_at=now),
        _statement_item(
            stable_key=capture_operation.stable_provider_key,
            occurred_at=now,
            operation_kind="CAPTURE",
            status="SUCCEEDED",
        ),
    ]
    raw_bytes = json.dumps({"items": items}, separators=(",", ":"), sort_keys=True).encode()
    import_arguments = ImportArguments(
        principal=principal,
        environment_public_id=environment.public_id,
        provider="PAYMENT_PROVIDER",
        source_reference=f"seeded-mismatches-{uuid.uuid4().hex}",
        source_format="JSON",
        period_start=now - timedelta(minutes=1),
        period_end=now + timedelta(minutes=1),
        raw_bytes=raw_bytes,
    )
    with factory() as session, session.begin():
        imported = import_statement(session, **import_arguments)
        run_id = imported.reconciliation_run.id

    claim = claim_reconciliation_run(factory, run_id=run_id)
    assert claim is not None
    assert process_reconciliation_claim(factory, claim)

    with factory() as session, session.begin():
        run = session.get(ReconciliationRun, run_id)
        assert run is not None
        assert run.status == "COMPLETED"
        mismatch_types = set(
            session.scalars(
                select(ReconciliationMismatch.mismatch_type).where(
                    ReconciliationMismatch.reconciliation_run_id == run_id
                )
            )
        )
        assert mismatch_types == {
            "MISSING_INTERNAL_TRANSACTION",
            "MISSING_PROVIDER_TRANSACTION",
            "AMOUNT_MISMATCH",
            "CURRENCY_MISMATCH",
            "STATUS_MISMATCH",
            "DUPLICATE_PROVIDER_EFFECT",
            "MISSING_INTERNAL_JOURNAL",
        }
        match_count = session.scalar(
            select(func.count())
            .select_from(ReconciliationMatch)
            .where(ReconciliationMatch.reconciliation_run_id == run_id)
        )
        mismatch_count = session.scalar(
            select(func.count())
            .select_from(ReconciliationMismatch)
            .where(ReconciliationMismatch.reconciliation_run_id == run_id)
        )
        evidence_count = session.scalar(
            select(func.count())
            .select_from(MismatchEvidenceVersion)
            .join(
                ReconciliationMismatch,
                ReconciliationMismatch.id == MismatchEvidenceVersion.reconciliation_mismatch_id,
            )
            .where(ReconciliationMismatch.reconciliation_run_id == run_id)
        )
        history_count = session.scalar(
            select(func.count())
            .select_from(MismatchWorkflowHistory)
            .join(
                ReconciliationMismatch,
                ReconciliationMismatch.id == MismatchWorkflowHistory.reconciliation_mismatch_id,
            )
            .where(ReconciliationMismatch.reconciliation_run_id == run_id)
        )
        assert match_count == 1
        assert mismatch_count == evidence_count == history_count == 9
        workflow_mismatch = session.scalar(
            select(ReconciliationMismatch).where(
                ReconciliationMismatch.reconciliation_run_id == run_id,
                ReconciliationMismatch.mismatch_type == "MISSING_INTERNAL_TRANSACTION",
            )
        )
        assert workflow_mismatch is not None
        workflow_mismatch_id = workflow_mismatch.public_id
        first_evidence = session.scalar(
            select(MismatchEvidenceVersion).where(
                MismatchEvidenceVersion.reconciliation_mismatch_id == workflow_mismatch.id,
                MismatchEvidenceVersion.version == 1,
            )
        )
        assert first_evidence is not None
        first_evidence_digest = first_evidence.evidence_sha256

    with factory() as session, session.begin():
        refreshed = refresh_mismatch_evidence(
            session,
            principal=principal,
            environment_public_id=environment.public_id,
            mismatch_public_id=workflow_mismatch_id,
        )
        assert refreshed.version == 2
    with factory() as session, session.begin():
        acknowledged = acknowledge_mismatch(
            session,
            principal=principal,
            environment_public_id=environment.public_id,
            mismatch_public_id=workflow_mismatch_id,
            note="Synthetic operator reviewed the immutable evidence.",
        )
        assert acknowledged.workflow_status == "ACKNOWLEDGED"
    with factory() as session, session.begin():
        resolved = resolve_mismatch(
            session,
            principal=principal,
            environment_public_id=environment.public_id,
            mismatch_public_id=workflow_mismatch_id,
            note="Synthetic discrepancy was resolved without changing payment outcome.",
        )
        assert resolved.workflow_status == "RESOLVED"
        assert resolved.compensating_journal_id is None
    with factory() as session, session.begin():
        with pytest.raises(RelayPayError) as invalid_transition:
            acknowledge_mismatch(
                session,
                principal=principal,
                environment_public_id=environment.public_id,
                mismatch_public_id=workflow_mismatch_id,
                note="A resolved mismatch cannot be acknowledged again.",
            )
        assert invalid_transition.value.code == "INVALID_MISMATCH_TRANSITION"
        persisted_versions = list(
            session.scalars(
                select(MismatchEvidenceVersion)
                .join(
                    ReconciliationMismatch,
                    ReconciliationMismatch.id == MismatchEvidenceVersion.reconciliation_mismatch_id,
                )
                .where(ReconciliationMismatch.public_id == workflow_mismatch_id)
                .order_by(MismatchEvidenceVersion.version)
            )
        )
        assert [version.version for version in persisted_versions] == [1, 2]
        assert persisted_versions[0].evidence_sha256 == first_evidence_digest
        workflow_history = list(
            session.scalars(
                select(MismatchWorkflowHistory)
                .join(
                    ReconciliationMismatch,
                    ReconciliationMismatch.id == MismatchWorkflowHistory.reconciliation_mismatch_id,
                )
                .where(ReconciliationMismatch.public_id == workflow_mismatch_id)
                .order_by(MismatchWorkflowHistory.created_at, MismatchWorkflowHistory.id)
            )
        )
        assert [entry.to_status for entry in workflow_history] == [
            "OPEN",
            "ACKNOWLEDGED",
            "RESOLVED",
        ]

    with factory() as session, session.begin():
        replay = import_statement(session, **import_arguments)
        assert not replay.created
        assert replay.reconciliation_run.id == run_id
    assert claim_reconciliation_run(factory, run_id=run_id) is None
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReconciliationMismatch)
                .where(ReconciliationMismatch.reconciliation_run_id == run_id)
            )
            == mismatch_count
        )
    engine.dispose()


def test_expired_reconciliation_lease_is_reclaimed_and_stale_claim_is_rejected() -> None:
    engine, principal, environment = _seed_identity()
    factory = build_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        imported = import_statement(
            session,
            principal=principal,
            environment_public_id=environment.public_id,
            provider="PAYMENT_PROVIDER",
            source_reference=f"lease-reclaim-{uuid.uuid4().hex}",
            source_format="JSON",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(minutes=1),
            raw_bytes=b'{"items":[]}',
        )
        run_id = imported.reconciliation_run.id
    stale = claim_reconciliation_run(factory, run_id=run_id, lease_seconds=-1)
    assert stale is not None
    reclaimed = claim_reconciliation_run(factory, run_id=run_id)
    assert reclaimed is not None
    assert reclaimed.lease_token != stale.lease_token
    assert not process_reconciliation_claim(factory, stale)
    assert process_reconciliation_claim(factory, reclaimed)
    with factory() as session, session.begin():
        run = session.get(ReconciliationRun, run_id)
        assert run is not None
        assert run.status == "COMPLETED"
        assert run.attempt_count == 2
    engine.dispose()


def test_statement_import_http_contract_enforces_csrf_replay_and_conflict() -> None:
    engine, principal, environment = _seed_identity()
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        user = session.get(User, principal.user_id)
        assert user is not None
        email = user.email_normalized
    settings = Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL=DATABASE_URL,
        PROVIDER_DATABASE_URL=(
            "postgresql+psycopg://provider_app:provider_app_dev@localhost:55432/provider"
        ),
        RECEIVER_DATABASE_URL=(
            "postgresql+psycopg://receiver_app:receiver_app_dev@localhost:55432/relaypay"
        ),
        SESSION_SECRET="m2-session-secret-for-tests-at-least-32-bytes",
        CSRF_SECRET="m2-csrf-secret-for-tests-at-least-32-bytes",
        API_KEY_PEPPER="m2-api-key-pepper-for-tests-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="m2-idempotency-pepper-for-tests",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-m2-http-tests",
        PROVIDER_SIGNING_SECRET="m2-provider-signing-test",
        PROVIDER_CONTROL_SECRET="m2-provider-control-test",
        RECEIVER_WEBHOOK_SECRET="m2-receiver-webhook-test",
    )
    now = datetime.now(UTC)
    raw_bytes = _statement(now)
    source_reference = f"http-import-{uuid.uuid4().hex}"
    form = {
        "provider": "PAYMENT_PROVIDER",
        "sourceReference": source_reference,
        "sourceFormat": "JSON",
        "periodStart": (now - timedelta(minutes=1)).isoformat(),
        "periodEnd": (now + timedelta(minutes=1)).isoformat(),
    }
    path = f"/api/admin/v1/environments/{environment.public_id}/statement-imports"
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/session/login",
            json={"email": email, "password": "Synthetic-M2-Admin-Password!"},
        )
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrfToken"]}
        first = client.post(
            path,
            headers=headers,
            data=form,
            files={"statement": ("statement.json", raw_bytes, "application/json")},
        )
        replay = client.post(
            path,
            headers=headers,
            data=form,
            files={"statement": ("statement.json", raw_bytes, "application/json")},
        )
        assert first.status_code == 201, first.text
        assert replay.status_code == 200, replay.text
        assert first.json() == replay.json()
        conflict = client.post(
            path,
            headers=headers,
            data=form,
            files={"statement": ("statement.json", _statement(now, amount=1), "application/json")},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "STATEMENT_SOURCE_CONFLICT"
        missing_csrf = client.post(
            path,
            data=form | {"sourceReference": f"no-csrf-{uuid.uuid4().hex}"},
            files={"statement": ("statement.json", raw_bytes, "application/json")},
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["error"]["code"] == "CSRF_INVALID"
    engine.dispose()
