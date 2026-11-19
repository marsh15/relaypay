"""M18: streamed COPY import and the chunked, resumable, set-based reconciliation (algorithm 2).

Algorithm 2 must classify exactly like algorithm 1 for the types they share, which the
differential test asserts on the same logical statement imported both ways.
"""

import csv
import hashlib
import io
import json
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.errors import RelayPayError
from relaypay.identity.models import User
from relaypay.provider_operations.models import ProviderOperation
from relaypay.reconciliation.models import (
    MismatchEvidenceVersion,
    ReconciliationMatch,
    ReconciliationMismatch,
    ReconciliationRun,
    StatementImport,
    StatementItem,
)
from relaypay.reconciliation.scale import (
    complete_chunked_reconciliation,
    import_streamed_statement,
)
from relaypay.reconciliation.service import (
    claim_reconciliation_run,
    import_statement,
    process_reconciliation_claim,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app
from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import World, build_world, paid_payment

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
BASE_HEADER = [
    "providerItemId",
    "stableKey",
    "operationKind",
    "amount",
    "currency",
    "status",
    "occurredAt",
]
FEE_HEADER = [*BASE_HEADER, "feeAmount", "taxAmount"]


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("reconciliation-scale-tests", pool_size=8) as session_factory:
        yield session_factory


@dataclass(frozen=True, slots=True)
class Op:
    stable_key: str
    kind: str
    amount: int


def _operations(factory: sessionmaker[Session], world: World, *public_ids: str) -> list[Op]:
    with factory() as session, session.begin():
        rows = session.scalars(
            select(ProviderOperation).where(
                ProviderOperation.organisation_id == world.organisation_id,
                ProviderOperation.public_id.in_(public_ids),
            )
        )
        return [Op(row.stable_provider_key, row.kind, 100_000) for row in rows]


def _item(
    op: Op | None,
    *,
    when: datetime,
    key: str | None = None,
    amount: int | None = None,
    status: str = "SUCCEEDED",
    kind: str | None = None,
    fee: int | None = None,
    tax: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "providerItemId": f"prov_{uuid.uuid4().hex}",
        "stableKey": key or (op.stable_key if op else f"capture:ghost_{uuid.uuid4().hex}"),
        "operationKind": kind or (op.kind if op else "CAPTURE"),
        "amount": amount if amount is not None else (op.amount if op else 100_000),
        "currency": "INR",
        "status": status,
        "occurredAt": when.isoformat(),
    }
    if fee is not None or tax is not None:
        row["feeAmount"] = fee
        row["taxAmount"] = tax
    return row


def _json_bytes(items: list[dict[str, object]]) -> bytes:
    base = [{k: v for k, v in i.items() if k in BASE_HEADER} for i in items]
    return json.dumps({"items": base}, separators=(",", ":"), sort_keys=True).encode()


def _csv_bytes(items: list[dict[str, object]], *, header: list[str] | None = None) -> bytes:
    columns = header or BASE_HEADER
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for item in items:
        writer.writerow(["" if item.get(c) is None else item[c] for c in columns])
    return out.getvalue().encode()


def _import_v2(
    factory: sessionmaker[Session],
    world: World,
    raw: bytes,
    *,
    when: datetime,
    reference: str | None = None,
    span: timedelta = timedelta(hours=1),
) -> tuple[StatementImport, ReconciliationRun, bool]:
    with tempfile.SpooledTemporaryFile() as body, factory() as session, session.begin():
        body.write(raw)
        body.seek(0)
        result = import_streamed_statement(
            session,
            principal=world.admin_principal,
            environment_public_id=world.environment_public_id,
            provider="PAYMENT_PROVIDER",
            source_reference=reference or f"stream-{uuid.uuid4().hex}",
            period_start=when - timedelta(hours=1),
            period_end=when + span,
            body=body,
            sha256=hashlib.sha256(raw).digest(),
        )
        session.expunge_all()
        return result.statement_import, result.reconciliation_run, result.created


def _import_v1(
    factory: sessionmaker[Session], world: World, raw: bytes, *, when: datetime
) -> ReconciliationRun:
    with factory() as session, session.begin():
        result = import_statement(
            session,
            principal=world.admin_principal,
            environment_public_id=world.environment_public_id,
            provider="PAYMENT_PROVIDER",
            source_reference=f"inline-{uuid.uuid4().hex}",
            source_format="JSON",
            period_start=when - timedelta(hours=1),
            period_end=when + timedelta(hours=1),
            raw_bytes=raw,
        )
        session.expunge_all()
        return result.reconciliation_run


def _run(factory: sessionmaker[Session], run: ReconciliationRun) -> None:
    claim = claim_reconciliation_run(factory, run_id=run.id)
    assert claim is not None
    assert process_reconciliation_claim(factory, claim)


@dataclass(frozen=True, slots=True)
class Outcome:
    matched_items: frozenset[str]
    mismatches: dict[str, dict[str, object]]  # subject_key -> evidence
    types: dict[str, str]


def _outcome(factory: sessionmaker[Session], run: ReconciliationRun) -> Outcome:
    with factory() as session, session.begin():
        matched = frozenset(
            session.scalars(
                select(StatementItem.provider_item_id)
                .join(
                    ReconciliationMatch, ReconciliationMatch.statement_item_id == StatementItem.id
                )
                .where(ReconciliationMatch.reconciliation_run_id == run.id)
            )
        )
        evidence: dict[str, dict[str, object]] = {}
        types: dict[str, str] = {}
        for mismatch in session.scalars(
            select(ReconciliationMismatch).where(
                ReconciliationMismatch.reconciliation_run_id == run.id
            )
        ):
            version = session.scalar(
                select(MismatchEvidenceVersion.evidence).where(
                    MismatchEvidenceVersion.reconciliation_mismatch_id == mismatch.id,
                    MismatchEvidenceVersion.version == 1,
                )
            )
            assert version is not None
            evidence[mismatch.subject_key] = version
            types[mismatch.subject_key] = mismatch.mismatch_type
        return Outcome(matched, evidence, types)


def _world_with_payments(
    factory: sessionmaker[Session], label: str, count: int = 5
) -> tuple[World, list[tuple[Op, Op]]]:
    world = build_world(factory, label=label)
    provider = FakeProvider()
    pairs = []
    for _ in range(count):
        paid = paid_payment(factory, world, provider)
        authorization, capture = _operations(
            factory, world, paid.authorization_operation_id, paid.capture_operation_id
        )
        # _operations returns rows unordered; pair them by kind
        if authorization.kind != "AUTHORIZE":
            authorization, capture = capture, authorization
        pairs.append((authorization, capture))
    return world, pairs


def _exception_statement(pairs: list[tuple[Op, Op]], when: datetime) -> list[dict[str, object]]:
    exact_auth, exact_capture = pairs[0]
    wrong_amount_auth, wrong_amount_capture = pairs[1]
    declined_auth, declined_capture = pairs[2]
    duplicated_auth, duplicated_capture = pairs[3]
    # pairs[4] is left off the statement entirely: both operations are MISSING_PROVIDER.
    return [
        _item(exact_auth, when=when),
        _item(exact_capture, when=when),
        _item(wrong_amount_auth, when=when),
        _item(wrong_amount_capture, when=when, amount=99_999),
        _item(declined_auth, when=when),
        _item(declined_capture, when=when, status="DECLINED"),
        _item(duplicated_auth, when=when),
        _item(duplicated_capture, when=when),
        _item(duplicated_capture, when=when),  # same stable key again
        _item(None, when=when),  # a provider effect RelayPay never recorded
    ]


def test_algorithm_two_classifies_exactly_like_algorithm_one(
    factory: sessionmaker[Session],
) -> None:
    world, pairs = _world_with_payments(factory, "recon-differential")
    when = datetime.now(UTC)
    items = _exception_statement(pairs, when)

    legacy_run = _import_v1(factory, world, _json_bytes(items), when=when)
    _run(factory, legacy_run)
    _, streamed_run, created = _import_v2(factory, world, _csv_bytes(items), when=when)
    assert created and streamed_run.algorithm_version == 2
    _run(factory, streamed_run)

    legacy = _outcome(factory, legacy_run)
    streamed = _outcome(factory, streamed_run)
    assert legacy.matched_items == streamed.matched_items
    assert legacy.types == streamed.types
    assert set(legacy.types.values()) >= {
        "AMOUNT_MISMATCH",
        "STATUS_MISMATCH",
        "DUPLICATE_PROVIDER_EFFECT",
        "MISSING_INTERNAL_TRANSACTION",
        "MISSING_PROVIDER_TRANSACTION",
    }
    # Same evidence documents, not only the same labels.
    for subject, document in legacy.mismatches.items():
        assert document == streamed.mismatches[subject], subject
    assert legacy.mismatches == streamed.mismatches
    with factory() as session, session.begin():
        run = session.get(ReconciliationRun, streamed_run.id)
        assert run is not None and run.status == "COMPLETED"
        assert run.matched_count == len(streamed.matched_items)
        assert run.mismatch_count == len(streamed.mismatches)


def test_fee_tax_and_timing_exceptions_exist_only_in_algorithm_two(
    factory: sessionmaker[Session],
) -> None:
    world, pairs = _world_with_payments(factory, "recon-fee-tax-timing", count=4)
    when = datetime.now(UTC)
    captures = [capture for _, capture in pairs]
    items = [
        _item(captures[0], when=when, fee=0, tax=0),  # equals what RelayPay charged: matches
        _item(captures[1], when=when, fee=250, tax=0),  # provider fee differs
        _item(captures[2], when=when, fee=0, tax=45),  # provider tax differs
        _item(captures[3], when=when + timedelta(days=2)),  # settled two days after the operation
    ]
    # The authorizations are deliberately left off; they become MISSING_PROVIDER_TRANSACTION rows.
    _, run, _ = _import_v2(
        factory, world, _csv_bytes(items, header=FEE_HEADER), when=when, span=timedelta(days=3)
    )
    _run(factory, run)
    outcome = _outcome(factory, run)

    by_type = sorted(set(outcome.types.values()))
    assert "FEE_MISMATCH" in by_type and "TAX_MISMATCH" in by_type and "TIMING" in by_type
    assert len(outcome.matched_items) == 1
    fee_subject = next(k for k, t in outcome.types.items() if t == "FEE_MISMATCH")
    statement_item = outcome.mismatches[fee_subject]["statementItem"]
    assert isinstance(statement_item, dict) and statement_item["feeAmount"] == 250
    internal = outcome.mismatches[fee_subject]["internal"]
    assert isinstance(internal, dict) and internal["feeAmount"] == 0


def test_a_run_that_stops_part_way_resumes_without_redoing_or_losing_chunks(
    factory: sessionmaker[Session],
) -> None:
    world, pairs = _world_with_payments(factory, "recon-resume")
    when = datetime.now(UTC)
    items = _exception_statement(pairs, when)

    reference_run = _import_v1(factory, world, _json_bytes(items), when=when)
    _run(factory, reference_run)
    expected = _outcome(factory, reference_run)

    _, run, _ = _import_v2(factory, world, _csv_bytes(items), when=when)
    claim = claim_reconciliation_run(factory, run_id=run.id)
    assert claim is not None
    # Three ordinals per chunk, then die after one chunk, as a crashed worker would.
    assert complete_chunked_reconciliation(
        factory, claim.run_id, claim.lease_token, chunk_ordinals=3, stop_after_chunks=1
    )
    with factory() as session, session.begin():
        partial = session.get(ReconciliationRun, run.id)
        assert partial is not None
        assert (partial.status, partial.progress_ordinal) == ("RUNNING", 3)

    assert complete_chunked_reconciliation(
        factory, claim.run_id, claim.lease_token, chunk_ordinals=3
    )
    resumed = _outcome(factory, run)

    assert resumed.matched_items == expected.matched_items
    assert resumed.types == expected.types
    with factory() as session, session.begin():
        done = session.get(ReconciliationRun, run.id)
        assert done is not None and done.status == "COMPLETED"
        assert done.progress_ordinal == len(items)
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReconciliationMatch)
                .where(ReconciliationMatch.reconciliation_run_id == run.id)
            )
            == done.matched_count
        )


def test_a_worker_that_lost_its_lease_cannot_advance_the_run(
    factory: sessionmaker[Session],
) -> None:
    world, pairs = _world_with_payments(factory, "recon-lease")
    when = datetime.now(UTC)
    _, run, _ = _import_v2(
        factory, world, _csv_bytes(_exception_statement(pairs, when)[:3]), when=when
    )
    claim = claim_reconciliation_run(factory, run_id=run.id)
    assert claim is not None

    assert not complete_chunked_reconciliation(factory, claim.run_id, uuid.uuid4())

    with factory() as session, session.begin():
        untouched = session.get(ReconciliationRun, run.id)
        assert untouched is not None
        assert (untouched.status, untouched.progress_ordinal) == ("RUNNING", 0)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        (b"", "header"),
        (b"providerItemId,stableKey\n", "headers"),
        (_csv_bytes([]), "no items"),
    ],
)
def test_structurally_invalid_statements_are_rejected_and_leave_nothing(
    factory: sessionmaker[Session], raw: bytes, fragment: str
) -> None:
    world = build_world(factory, label="recon-invalid-structure")
    with pytest.raises(RelayPayError) as refused:
        _import_v2(factory, world, raw, when=datetime.now(UTC))
    assert refused.value.code == "INVALID_STATEMENT"
    assert fragment in refused.value.message.lower()
    _assert_nothing_stored(factory, world)


def _assert_nothing_stored(factory: sessionmaker[Session], world: World) -> None:
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatementImport)
                .where(StatementImport.organisation_id == world.organisation_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(StatementItem)
                .where(StatementItem.organisation_id == world.organisation_id)
            )
            == 0
        )


def test_row_level_problems_name_the_row_and_never_echo_its_content(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="recon-invalid-rows")
    when = datetime.now(UTC)
    good = _item(None, when=when)
    cases = {
        "amount": {**good, "providerItemId": "p-2", "amount": "-5"},
        "currency": {**good, "providerItemId": "p-2", "currency": "inr"},
        "status": {**good, "providerItemId": "p-2", "status": "SETTLED"},
        "operationKind": {**good, "providerItemId": "p-2", "operationKind": "CHARGEBACK"},
        "occurredAt": {**good, "providerItemId": "p-2", "occurredAt": "yesterday secret-token"},
    }
    for field, bad in cases.items():
        with pytest.raises(RelayPayError) as refused:
            _import_v2(factory, world, _csv_bytes([good, bad]), when=when)
        assert refused.value.code == "INVALID_STATEMENT"
        assert f"row 2 has an invalid {field}" in refused.value.message
        assert "secret-token" not in refused.value.message
    _assert_nothing_stored(factory, world)


def test_duplicate_item_ids_and_out_of_period_items_are_rejected(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="recon-invalid-set")
    when = datetime.now(UTC)
    one = _item(None, when=when)
    with pytest.raises(RelayPayError) as duplicated:
        _import_v2(factory, world, _csv_bytes([one, one]), when=when)
    assert "unique" in duplicated.value.message

    late = _item(None, when=when + timedelta(days=30))
    with pytest.raises(RelayPayError) as outside:
        _import_v2(factory, world, _csv_bytes([late]), when=when)
    assert "outside the declared period" in outside.value.message
    _assert_nothing_stored(factory, world)


def test_repeating_an_import_replays_and_changed_bytes_conflict(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="recon-replay")
    when = datetime.now(UTC)
    raw = _csv_bytes([_item(None, when=when)])
    reference = f"replay-{uuid.uuid4().hex}"

    first_import, first_run, created = _import_v2(
        factory, world, raw, when=when, reference=reference
    )
    again_import, again_run, created_again = _import_v2(
        factory, world, raw, when=when, reference=reference
    )

    assert created and not created_again
    assert (first_import.id, first_run.id) == (again_import.id, again_run.id)
    assert first_import.streamed and first_import.raw_bytes is None

    with pytest.raises(RelayPayError) as conflict:
        _import_v2(
            factory,
            world,
            _csv_bytes([_item(None, when=when)]),
            when=when,
            reference=reference,
        )
    assert conflict.value.code == "STATEMENT_SOURCE_CONFLICT"


def test_http_streamed_import_needs_csrf_streams_the_body_and_replays(
    factory: sessionmaker[Session],
) -> None:
    world = build_world(factory, label="recon-http")
    with factory() as session, session.begin():
        user = session.get(User, world.admin_principal.user_id)
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
        SESSION_SECRET="scale-session-secret-for-tests-at-least-32-bytes",
        CSRF_SECRET="scale-csrf-secret-for-tests-at-least-32-bytes",
        API_KEY_PEPPER="scale-api-key-pepper-for-tests-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="scale-idempotency-pepper-for-tests",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-scale-http-tests",
        PROVIDER_SIGNING_SECRET="scale-provider-signing-test",
        PROVIDER_CONTROL_SECRET="scale-provider-control-test",
        RECEIVER_WEBHOOK_SECRET="scale-receiver-webhook-test",
    )
    when = datetime.now(UTC)
    raw = _csv_bytes([_item(None, when=when) for _ in range(50)])
    path = f"/api/admin/v1/environments/{world.environment_public_id}/statement-imports/stream"
    params = {
        "sourceReference": f"http-stream-{uuid.uuid4().hex}",
        "periodStart": (when - timedelta(hours=1)).isoformat(),
        "periodEnd": (when + timedelta(hours=1)).isoformat(),
    }
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/session/login",
            json={"email": email, "password": "Synthetic-World-Admin-Password!"},
        )
        assert login.status_code == 200, login.text
        headers = {"X-CSRF-Token": login.json()["csrfToken"], "Content-Type": "text/csv"}

        missing = client.post(path, params=params, content=raw)
        first = client.post(path, params=params, headers=headers, content=raw)
        replay = client.post(path, params=params, headers=headers, content=raw)
        changed = client.post(
            path, params=params, headers=headers, content=_csv_bytes([_item(None, when=when)])
        )

    assert missing.status_code == 403 and missing.json()["error"]["code"] == "CSRF_INVALID"
    assert first.status_code == 201, first.text
    assert first.json()["algorithmVersion"] == 2 and first.json()["runStatus"] == "PENDING"
    assert replay.status_code == 200 and replay.json() == first.json()
    assert changed.status_code == 409
