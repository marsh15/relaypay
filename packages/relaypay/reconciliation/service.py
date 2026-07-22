import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError, not_found
from relaypay.identity.models import Environment
from relaypay.identity.security import Principal
from relaypay.identity.service import append_audit, require_organisation_admin
from relaypay.ids import new_public_id
from relaypay.reconciliation.models import ReconciliationRun, StatementImport, StatementItem

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
    if currency != "INR":
        raise _invalid_statement("Statement currency is unsupported")
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
