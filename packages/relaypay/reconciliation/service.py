import csv
import hashlib
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment
from relaypay.identity.security import Principal
from relaypay.identity.service import append_audit, require_organisation_admin
from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import Journal
from relaypay.payments.models import Authorization, Capture, Refund
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

MAX_STATEMENT_BYTES = 1_048_576
MAX_STATEMENT_ITEMS = 10_000
RECONCILIATION_ALGORITHM_VERSION = 1
_FIELDS = {
    "providerItemId",
    "stableKey",
    "operationKind",
    "amount",
    "currency",
    "status",
    "occurredAt",
}


@dataclass(frozen=True, slots=True)
class ParsedStatementItem:
    provider_item_id: str
    stable_key: str
    operation_kind: str
    amount: int
    currency: str
    provider_status: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class StatementImportResult:
    statement_import: StatementImport
    reconciliation_run: ReconciliationRun
    created: bool


@dataclass(frozen=True, slots=True)
class ReconciliationClaim:
    run_id: uuid.UUID
    lease_token: uuid.UUID


@dataclass(frozen=True, slots=True)
class InternalEffect:
    amount: int
    currency: str
    status: str
    journal_id: uuid.UUID | None
    resource_public_id: str


def _invalid_statement(message: str) -> RelayPayError:
    return RelayPayError(code="INVALID_STATEMENT", message=message, http_status=422)


def _required_text(row: dict[str, object], field: str, *, max_length: int = 128) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _invalid_statement(f"Statement field {field} is invalid")
    return value


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise _invalid_statement("Statement field occurredAt is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _invalid_statement("Statement field occurredAt is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid_statement("Statement field occurredAt must include a timezone")
    return parsed


def _parse_row(row: dict[str, object]) -> ParsedStatementItem:
    if set(row) != _FIELDS:
        raise _invalid_statement("Statement item fields do not match the supported schema")
    amount_value = row.get("amount")
    if isinstance(amount_value, str):
        try:
            amount = int(amount_value)
        except ValueError as error:
            raise _invalid_statement("Statement field amount is invalid") from error
    elif isinstance(amount_value, int) and not isinstance(amount_value, bool):
        amount = amount_value
    else:
        raise _invalid_statement("Statement field amount is invalid")
    if amount <= 0:
        raise _invalid_statement("Statement field amount must be positive")
    operation_kind = _required_text(row, "operationKind", max_length=16)
    if operation_kind not in {"AUTHORIZE", "CAPTURE", "REFUND"}:
        raise _invalid_statement("Statement operation kind is unsupported")
    currency = _required_text(row, "currency", max_length=3)
    if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise _invalid_statement("Statement currency must be an uppercase three-letter code")
    provider_status = _required_text(row, "status", max_length=16)
    if provider_status not in {"PENDING", "SUCCEEDED", "DECLINED"}:
        raise _invalid_statement("Statement provider status is unsupported")
    return ParsedStatementItem(
        provider_item_id=_required_text(row, "providerItemId"),
        stable_key=_required_text(row, "stableKey"),
        operation_kind=operation_kind,
        amount=amount,
        currency=currency,
        provider_status=provider_status,
        occurred_at=_parse_time(row.get("occurredAt")),
    )


def parse_statement(raw_bytes: bytes, source_format: str) -> list[ParsedStatementItem]:
    if not raw_bytes or len(raw_bytes) > MAX_STATEMENT_BYTES:
        raise _invalid_statement("Statement size is outside the supported bounds")
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _invalid_statement("Statement must be UTF-8 encoded") from error
    if "\x00" in text:
        raise _invalid_statement("Statement contains an unsupported null character")
    rows: list[dict[str, object]]
    if source_format == "JSON":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise _invalid_statement("Statement JSON is malformed") from error
        if not isinstance(document, dict) or set(document) != {"items"}:
            raise _invalid_statement("Statement JSON must contain only an items array")
        items_value = document["items"]
        if not isinstance(items_value, list):
            raise _invalid_statement("Statement JSON items must be an array")
        if not all(isinstance(item, dict) for item in items_value):
            raise _invalid_statement("Statement JSON items must be objects")
        rows = [cast(dict[str, object], item) for item in items_value]
    elif source_format == "CSV":
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            if reader.fieldnames is None or set(reader.fieldnames) != _FIELDS:
                raise _invalid_statement("Statement CSV headers do not match the supported schema")
            csv_rows = list(reader)
        except csv.Error as error:
            raise _invalid_statement("Statement CSV is malformed") from error
        if any(None in row for row in csv_rows):
            raise _invalid_statement("Statement CSV row has too many fields")
        rows = [cast(dict[str, object], row) for row in csv_rows]
    else:
        raise _invalid_statement("Statement format must be CSV or JSON")
    if len(rows) > MAX_STATEMENT_ITEMS:
        raise _invalid_statement("Statement contains too many items")
    parsed = [_parse_row(row) for row in rows]
    provider_item_ids = {item.provider_item_id for item in parsed}
    if len(provider_item_ids) != len(parsed):
        raise _invalid_statement("Statement provider item identifiers must be unique")
    return parsed


def import_statement(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    provider: str,
    source_reference: str,
    source_format: str,
    period_start: datetime,
    period_end: datetime,
    raw_bytes: bytes,
) -> StatementImportResult:
    require_organisation_admin(principal)
    if provider != "PAYMENT_PROVIDER":
        raise _invalid_statement("Statement provider is unsupported")
    if not source_reference or len(source_reference) > 128:
        raise _invalid_statement("Statement source reference is invalid")
    if period_start.tzinfo is None or period_end.tzinfo is None or period_end <= period_start:
        raise _invalid_statement("Statement period is invalid")
    normalized_format = source_format.upper()
    digest = hashlib.sha256(raw_bytes).digest()
    environment = session.scalar(
        select(Environment).where(
            Environment.organisation_id == principal.organisation_id,
            Environment.public_id == environment_public_id,
            Environment.status == "ACTIVE",
        )
    )
    if environment is None:
        raise not_found("Environment")
    source_lock_material = (
        f"{principal.organisation_id}:{environment.id}:{provider}:{source_reference}".encode()
    )
    source_lock_key = int.from_bytes(
        hashlib.sha256(source_lock_material).digest()[:8], byteorder="big", signed=True
    )
    session.execute(select(func.pg_advisory_xact_lock(source_lock_key)))
    existing = session.scalar(
        select(StatementImport)
        .where(
            StatementImport.organisation_id == principal.organisation_id,
            StatementImport.environment_id == environment.id,
            StatementImport.provider == provider,
            StatementImport.source_reference == source_reference,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.raw_sha256 != digest:
            raise RelayPayError(
                code="STATEMENT_SOURCE_CONFLICT",
                message="Statement source reference is bound to different bytes",
                http_status=409,
            )
        run = session.scalar(
            select(ReconciliationRun).where(
                ReconciliationRun.statement_import_id == existing.id,
                ReconciliationRun.algorithm_version == RECONCILIATION_ALGORITHM_VERSION,
            )
        )
        if run is None:
            raise RuntimeError("statement import is missing its reconciliation run")
        return StatementImportResult(existing, run, False)

    parsed = parse_statement(raw_bytes, normalized_format)
    if any(item.occurred_at < period_start or item.occurred_at >= period_end for item in parsed):
        raise _invalid_statement("Statement item occurrence is outside the declared period")
    statement_import = StatementImport(
        public_id=new_public_id("stmt"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        provider=provider,
        source_reference=source_reference,
        source_format=normalized_format,
        period_start=period_start,
        period_end=period_end,
        raw_bytes=raw_bytes,
        raw_sha256=digest,
    )
    session.add(statement_import)
    session.flush()
    session.add_all(
        [
            StatementItem(
                organisation_id=principal.organisation_id,
                environment_id=environment.id,
                statement_import_id=statement_import.id,
                ordinal=ordinal,
                provider_item_id=item.provider_item_id,
                stable_key=item.stable_key,
                operation_kind=item.operation_kind,
                amount=item.amount,
                currency=item.currency,
                provider_status=item.provider_status,
                occurred_at=item.occurred_at,
            )
            for ordinal, item in enumerate(parsed, 1)
        ]
    )
    run = ReconciliationRun(
        public_id=new_public_id("rrun"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        statement_import_id=statement_import.id,
        algorithm_version=RECONCILIATION_ALGORITHM_VERSION,
        status="PENDING",
        attempt_count=0,
    )
    session.add(run)
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="STATEMENT_IMPORTED",
        target_type="STATEMENT_IMPORT",
        target_id=statement_import.public_id,
        details={
            "algorithmVersion": RECONCILIATION_ALGORITHM_VERSION,
            "format": normalized_format,
            "itemCount": len(parsed),
            "provider": provider,
            "sha256": digest.hex(),
            "sourceReference": source_reference,
        },
    )
    session.flush()
    return StatementImportResult(statement_import, run, True)


def claim_reconciliation_run(
    factory: sessionmaker[Session],
    *,
    lease_seconds: int = 30,
    run_id: uuid.UUID | None = None,
) -> ReconciliationClaim | None:
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        run = session.scalar(
            select(ReconciliationRun)
            .where(
                ReconciliationRun.id == run_id if run_id is not None else true(),
                or_(
                    ReconciliationRun.status == "PENDING",
                    and_(
                        ReconciliationRun.status == "RUNNING",
                        ReconciliationRun.lease_expires_at <= now,
                    ),
                ),
            )
            .order_by(ReconciliationRun.created_at, ReconciliationRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        token = new_uuid()
        run.status = "RUNNING"
        run.attempt_count += 1
        run.lease_token = token
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.started_at = run.started_at or now
        run.completed_at = None
        run.safe_error_code = None
        return ReconciliationClaim(run.id, token)


def _provider_status(internal_status: str) -> str:
    if internal_status == "SUCCEEDED":
        return "SUCCEEDED"
    if internal_status == "FAILED":
        return "DECLINED"
    return "PENDING"


def _internal_effects(
    session: Session, operation_ids: set[uuid.UUID]
) -> dict[uuid.UUID, InternalEffect]:
    if not operation_ids:
        return {}
    effects: dict[uuid.UUID, InternalEffect] = {}
    for authorization in session.scalars(
        select(Authorization).where(Authorization.provider_operation_id.in_(operation_ids))
    ):
        effects[authorization.provider_operation_id] = InternalEffect(
            authorization.amount,
            authorization.currency,
            authorization.status,
            None,
            authorization.public_id,
        )
    for capture in session.scalars(
        select(Capture).where(Capture.provider_operation_id.in_(operation_ids))
    ):
        effects[capture.provider_operation_id] = InternalEffect(
            capture.amount,
            capture.currency,
            capture.status,
            capture.journal_id,
            capture.public_id,
        )
    for refund in session.scalars(
        select(Refund).where(Refund.provider_operation_id.in_(operation_ids))
    ):
        effects[refund.provider_operation_id] = InternalEffect(
            refund.amount,
            refund.currency,
            refund.status,
            refund.journal_id,
            refund.public_id,
        )
    return effects


def _evidence(
    *,
    statement_item: StatementItem | None,
    operation: ProviderOperation | None,
    internal: InternalEffect | None,
    reason: str,
) -> dict[str, object]:
    return {
        "internal": (
            {
                "amount": internal.amount,
                "currency": internal.currency,
                "journalId": str(internal.journal_id) if internal.journal_id else None,
                "resourceId": internal.resource_public_id,
                "status": internal.status,
            }
            if internal is not None
            else None
        ),
        "providerOperation": (
            {
                "id": operation.public_id,
                "kind": operation.kind,
                "stableKey": operation.stable_provider_key,
                "status": operation.status,
            }
            if operation is not None
            else None
        ),
        "reason": reason,
        "statementItem": (
            {
                "amount": statement_item.amount,
                "currency": statement_item.currency,
                "id": statement_item.provider_item_id,
                "kind": statement_item.operation_kind,
                "stableKey": statement_item.stable_key,
                "status": statement_item.provider_status,
            }
            if statement_item is not None
            else None
        ),
    }


def _add_mismatch(
    session: Session,
    *,
    run: ReconciliationRun,
    subject_key: str,
    mismatch_type: str,
    statement_item: StatementItem | None,
    operation: ProviderOperation | None,
    internal: InternalEffect | None,
    reason: str,
) -> None:
    evidence = _evidence(
        statement_item=statement_item,
        operation=operation,
        internal=internal,
        reason=reason,
    )
    mismatch = ReconciliationMismatch(
        public_id=new_public_id("mis"),
        organisation_id=run.organisation_id,
        environment_id=run.environment_id,
        reconciliation_run_id=run.id,
        subject_key=subject_key,
        mismatch_type=mismatch_type,
        statement_item_id=statement_item.id if statement_item else None,
        provider_operation_id=operation.id if operation else None,
        workflow_status="OPEN",
    )
    session.add(mismatch)
    session.flush([mismatch])
    session.add_all(
        [
            MismatchEvidenceVersion(
                organisation_id=run.organisation_id,
                environment_id=run.environment_id,
                reconciliation_mismatch_id=mismatch.id,
                version=1,
                evidence=evidence,
                evidence_sha256=hashlib.sha256(canonical_json_bytes(evidence)).digest(),
            ),
            MismatchWorkflowHistory(
                organisation_id=run.organisation_id,
                environment_id=run.environment_id,
                reconciliation_mismatch_id=mismatch.id,
                from_status=None,
                to_status="OPEN",
                actor_id=None,
                note=None,
            ),
        ]
    )


def _complete_reconciliation(session: Session, claim: ReconciliationClaim) -> bool:
    run = session.scalar(
        select(ReconciliationRun)
        .where(
            ReconciliationRun.id == claim.run_id,
            ReconciliationRun.status == "RUNNING",
            ReconciliationRun.lease_token == claim.lease_token,
        )
        .with_for_update()
    )
    if run is None:
        return False
    statement_import = session.get(StatementImport, run.statement_import_id)
    if statement_import is None:
        raise RuntimeError("reconciliation run has no statement import")
    items = list(
        session.scalars(
            select(StatementItem)
            .where(StatementItem.statement_import_id == statement_import.id)
            .order_by(StatementItem.ordinal)
        )
    )
    stable_keys = {item.stable_key for item in items}
    operations = list(
        session.scalars(
            select(ProviderOperation).where(
                ProviderOperation.organisation_id == run.organisation_id,
                ProviderOperation.environment_id == run.environment_id,
                or_(
                    ProviderOperation.stable_provider_key.in_(stable_keys),
                    and_(
                        ProviderOperation.created_at >= statement_import.period_start,
                        ProviderOperation.created_at < statement_import.period_end,
                    ),
                ),
            )
        )
    )
    operation_by_key = {operation.stable_provider_key: operation for operation in operations}
    internal_by_operation = _internal_effects(session, {operation.id for operation in operations})
    key_counts: dict[str, int] = {}
    for item in items:
        key_counts[item.stable_key] = key_counts.get(item.stable_key, 0) + 1

    for item in items:
        operation = operation_by_key.get(item.stable_key)
        internal = internal_by_operation.get(operation.id) if operation else None
        if key_counts[item.stable_key] > 1:
            _add_mismatch(
                session,
                run=run,
                subject_key=f"statement:{item.provider_item_id}:DUPLICATE_PROVIDER_EFFECT",
                mismatch_type="DUPLICATE_PROVIDER_EFFECT",
                statement_item=item,
                operation=operation,
                internal=internal,
                reason="statement contains the stable provider key more than once",
            )
            continue
        if operation is None or internal is None or operation.kind != item.operation_kind:
            _add_mismatch(
                session,
                run=run,
                subject_key=f"statement:{item.provider_item_id}:MISSING_INTERNAL_TRANSACTION",
                mismatch_type="MISSING_INTERNAL_TRANSACTION",
                statement_item=item,
                operation=operation,
                internal=internal,
                reason="no matching internal provider operation and payment child were found",
            )
            continue
        mismatch_count = 0
        comparisons = (
            ("AMOUNT_MISMATCH", item.amount != internal.amount, "amounts differ"),
            ("CURRENCY_MISMATCH", item.currency != internal.currency, "currencies differ"),
            (
                "STATUS_MISMATCH",
                item.provider_status != _provider_status(internal.status)
                or item.provider_status != _provider_status(operation.status),
                "provider and internal statuses differ",
            ),
            (
                "MISSING_INTERNAL_JOURNAL",
                item.provider_status == "SUCCEEDED"
                and item.operation_kind in {"CAPTURE", "REFUND"}
                and internal.journal_id is None,
                "successful money movement has no internal journal",
            ),
        )
        for mismatch_type, differs, reason in comparisons:
            if not differs:
                continue
            mismatch_count += 1
            _add_mismatch(
                session,
                run=run,
                subject_key=f"statement:{item.provider_item_id}:{mismatch_type}",
                mismatch_type=mismatch_type,
                statement_item=item,
                operation=operation,
                internal=internal,
                reason=reason,
            )
        if mismatch_count == 0:
            session.add(
                ReconciliationMatch(
                    organisation_id=run.organisation_id,
                    environment_id=run.environment_id,
                    reconciliation_run_id=run.id,
                    statement_item_id=item.id,
                    provider_operation_id=operation.id,
                    journal_id=internal.journal_id,
                    match_type=(
                        "DECLINED_WITHOUT_JOURNAL"
                        if item.provider_status == "DECLINED"
                        else "EXACT"
                    ),
                    evidence=_evidence(
                        statement_item=item,
                        operation=operation,
                        internal=internal,
                        reason="statement and internal evidence match",
                    ),
                )
            )

    for operation in operations:
        if (
            not (
                statement_import.period_start <= operation.created_at < statement_import.period_end
            )
            or operation.stable_provider_key in stable_keys
        ):
            continue
        internal = internal_by_operation.get(operation.id)
        _add_mismatch(
            session,
            run=run,
            subject_key=f"operation:{operation.public_id}:MISSING_PROVIDER_TRANSACTION",
            mismatch_type="MISSING_PROVIDER_TRANSACTION",
            statement_item=None,
            operation=operation,
            internal=internal,
            reason="internal provider operation is absent from the statement period",
        )

    run.status = "COMPLETED"
    run.lease_token = None
    run.lease_expires_at = None
    run.completed_at = datetime.now(UTC)
    run.safe_error_code = None
    return True


def process_reconciliation_claim(
    factory: sessionmaker[Session], claim: ReconciliationClaim
) -> bool:
    try:
        with factory() as session, session.begin():
            return _complete_reconciliation(session, claim)
    except Exception:
        with factory() as session, session.begin():
            run = session.scalar(
                select(ReconciliationRun)
                .where(
                    ReconciliationRun.id == claim.run_id,
                    ReconciliationRun.status == "RUNNING",
                    ReconciliationRun.lease_token == claim.lease_token,
                )
                .with_for_update()
            )
            if run is not None:
                run.status = "FAILED"
                run.lease_token = None
                run.lease_expires_at = None
                run.completed_at = datetime.now(UTC)
                run.safe_error_code = "RECONCILIATION_FAILED"
        return False


def run_reconciliation_batch(
    factory: sessionmaker[Session], *, limit: int = 10, lease_seconds: int = 30
) -> int:
    completed = 0
    for _ in range(limit):
        claim = claim_reconciliation_run(factory, lease_seconds=lease_seconds)
        if claim is None:
            break
        if process_reconciliation_claim(factory, claim):
            completed += 1
    return completed


def _workflow_note(note: str) -> str:
    normalized = note.strip()
    if not normalized or len(normalized) > 1000:
        raise RelayPayError(
            code="INVALID_WORKFLOW_NOTE",
            message="Workflow note must contain between 1 and 1000 characters",
            http_status=422,
        )
    return normalized


def _scoped_mismatch(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    mismatch_public_id: str,
    lock: bool = False,
) -> ReconciliationMismatch:
    query = (
        select(ReconciliationMismatch)
        .join(Environment, Environment.id == ReconciliationMismatch.environment_id)
        .where(
            ReconciliationMismatch.organisation_id == principal.organisation_id,
            Environment.public_id == environment_public_id,
            ReconciliationMismatch.public_id == mismatch_public_id,
        )
    )
    if lock:
        query = query.with_for_update(of=ReconciliationMismatch)
    mismatch = session.scalar(query)
    if mismatch is None:
        raise not_found("Reconciliation mismatch")
    return mismatch


def acknowledge_mismatch(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    mismatch_public_id: str,
    note: str,
) -> ReconciliationMismatch:
    require_organisation_admin(principal)
    normalized_note = _workflow_note(note)
    mismatch = _scoped_mismatch(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        mismatch_public_id=mismatch_public_id,
        lock=True,
    )
    if mismatch.workflow_status != "OPEN":
        raise RelayPayError(
            code="INVALID_MISMATCH_TRANSITION",
            message="Only an open mismatch can be acknowledged",
            http_status=409,
        )
    mismatch.workflow_status = "ACKNOWLEDGED"
    mismatch.acknowledgement_note = normalized_note
    session.add(
        MismatchWorkflowHistory(
            organisation_id=mismatch.organisation_id,
            environment_id=mismatch.environment_id,
            reconciliation_mismatch_id=mismatch.id,
            from_status="OPEN",
            to_status="ACKNOWLEDGED",
            actor_id=principal.user_id,
            note=normalized_note,
        )
    )
    append_audit(
        session,
        principal=principal,
        environment_id=mismatch.environment_id,
        action="RECONCILIATION_MISMATCH_ACKNOWLEDGED",
        target_type="RECONCILIATION_MISMATCH",
        target_id=mismatch.public_id,
    )
    return mismatch


def resolve_mismatch(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    mismatch_public_id: str,
    note: str,
    compensating_journal_public_id: str | None = None,
) -> ReconciliationMismatch:
    require_organisation_admin(principal)
    normalized_note = _workflow_note(note)
    mismatch = _scoped_mismatch(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        mismatch_public_id=mismatch_public_id,
        lock=True,
    )
    if mismatch.workflow_status != "ACKNOWLEDGED":
        raise RelayPayError(
            code="INVALID_MISMATCH_TRANSITION",
            message="Only an acknowledged mismatch can be resolved",
            http_status=409,
        )
    journal: Journal | None = None
    if compensating_journal_public_id is not None:
        journal = session.scalar(
            select(Journal).where(
                Journal.organisation_id == principal.organisation_id,
                Journal.environment_id == mismatch.environment_id,
                Journal.public_id == compensating_journal_public_id,
                Journal.journal_type == "COMPENSATION",
            )
        )
        if journal is None:
            raise not_found("Compensating journal")
    mismatch.workflow_status = "RESOLVED"
    mismatch.resolution_note = normalized_note
    mismatch.compensating_journal_id = journal.id if journal else None
    mismatch.resolved_at = datetime.now(UTC)
    session.add(
        MismatchWorkflowHistory(
            organisation_id=mismatch.organisation_id,
            environment_id=mismatch.environment_id,
            reconciliation_mismatch_id=mismatch.id,
            from_status="ACKNOWLEDGED",
            to_status="RESOLVED",
            actor_id=principal.user_id,
            note=normalized_note,
        )
    )
    append_audit(
        session,
        principal=principal,
        environment_id=mismatch.environment_id,
        action="RECONCILIATION_MISMATCH_RESOLVED",
        target_type="RECONCILIATION_MISMATCH",
        target_id=mismatch.public_id,
        details={"compensatingJournalId": compensating_journal_public_id},
    )
    return mismatch


def refresh_mismatch_evidence(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    mismatch_public_id: str,
) -> MismatchEvidenceVersion:
    require_organisation_admin(principal)
    mismatch = _scoped_mismatch(
        session,
        principal=principal,
        environment_public_id=environment_public_id,
        mismatch_public_id=mismatch_public_id,
        lock=True,
    )
    statement_item = (
        session.get(StatementItem, mismatch.statement_item_id)
        if mismatch.statement_item_id is not None
        else None
    )
    operation = (
        session.get(ProviderOperation, mismatch.provider_operation_id)
        if mismatch.provider_operation_id is not None
        else None
    )
    internal = (
        _internal_effects(session, {operation.id}).get(operation.id)
        if operation is not None
        else None
    )
    evidence = _evidence(
        statement_item=statement_item,
        operation=operation,
        internal=internal,
        reason=f"evidence refreshed for {mismatch.mismatch_type}",
    )
    next_version = (
        session.scalar(
            select(func.max(MismatchEvidenceVersion.version)).where(
                MismatchEvidenceVersion.reconciliation_mismatch_id == mismatch.id
            )
        )
        or 0
    ) + 1
    version = MismatchEvidenceVersion(
        organisation_id=mismatch.organisation_id,
        environment_id=mismatch.environment_id,
        reconciliation_mismatch_id=mismatch.id,
        version=next_version,
        evidence=evidence,
        evidence_sha256=hashlib.sha256(canonical_json_bytes(evidence)).digest(),
    )
    session.add(version)
    append_audit(
        session,
        principal=principal,
        environment_id=mismatch.environment_id,
        action="RECONCILIATION_EVIDENCE_REFRESHED",
        target_type="RECONCILIATION_MISMATCH",
        target_id=mismatch.public_id,
        details={"version": next_version},
    )
    session.flush([version])
    return version


def list_mismatches(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    workflow_status: str | None = None,
) -> list[ReconciliationMismatch]:
    query = (
        select(ReconciliationMismatch)
        .join(Environment, Environment.id == ReconciliationMismatch.environment_id)
        .where(
            ReconciliationMismatch.organisation_id == principal.organisation_id,
            Environment.public_id == environment_public_id,
        )
        .order_by(ReconciliationMismatch.created_at, ReconciliationMismatch.id)
    )
    if workflow_status is not None:
        if workflow_status not in {"OPEN", "ACKNOWLEDGED", "RESOLVED"}:
            raise RelayPayError(
                code="INVALID_WORKFLOW_STATUS",
                message="Mismatch workflow status is invalid",
                http_status=422,
            )
        query = query.where(ReconciliationMismatch.workflow_status == workflow_status)
    return list(session.scalars(query))
