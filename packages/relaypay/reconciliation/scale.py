"""Reconciliation at scale (M18): streamed COPY import and chunked, resumable, set-based matching.

Algorithm 1 (``service.py``) reads a bounded statement into memory and matches it in one
transaction. That is right for a 10,000-row file and wrong for a million. Algorithm 2 here:

* **Import** never holds the statement in memory. The body is spooled to disk while it is hashed,
  then streamed with ``COPY`` into a temporary staging table (the application role has no DDL on
  permanent tables, and a temporary table is dropped on commit), validated with set-based SQL,
  and copied into ``statement_items`` with one ``INSERT ... SELECT``.
* **Matching** walks the statement in chunks of ``CHUNK_ORDINALS`` rows. Each chunk is one
  transaction: classify every row against the internal ledger in a single SQL statement, insert
  all exact matches with ``INSERT ... SELECT``, and bring only the *exceptions* into Python to
  record them with their evidence. The chunk also advances ``reconciliation_runs.progress_ordinal``
  and the lease, so a run that dies resumes at the first uncommitted chunk instead of restarting.
* Exceptions are the algorithm-1 types plus ``FEE_MISMATCH``, ``TAX_MISMATCH`` and ``TIMING``.
  Rows missing from the statement (``MISSING_PROVIDER_TRANSACTION``) are found by an anti-join in
  chunks that are idempotent by construction (a row that already has its mismatch is skipped).

Both algorithms classify identically for the shared types, which a differential test asserts.
"""

import csv
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import IO, Any

from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import TextClause

from relaypay.errors import RelayPayError, not_found
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import AuditRecord, Environment
from relaypay.identity.security import Principal
from relaypay.identity.service import append_audit, require_organisation_admin
from relaypay.ids import new_public_id, new_uuid
from relaypay.reconciliation.models import (
    MismatchEvidenceVersion,
    MismatchWorkflowHistory,
    ReconciliationMismatch,
    ReconciliationRun,
    StatementImport,
    StatementItem,
)
from relaypay.reconciliation.service import StatementImportResult

STREAM_ALGORITHM_VERSION = 2
CHUNK_ORDINALS = 50_000
MAX_STREAMED_BYTES = 512 * 1024 * 1024
MAX_STREAMED_ITEMS = 10_000_000
# A statement time further than this from when RelayPay recorded the operation is a TIMING
# exception: it usually means the provider settled the item on a different day than RelayPay saw.
TIMING_TOLERANCE = timedelta(days=1)
_COPY_BLOCK_BYTES = 256 * 1024

_BASE_HEADER = (
    "providerItemId",
    "stableKey",
    "operationKind",
    "amount",
    "currency",
    "status",
    "occurredAt",
)
_FEE_HEADER = (*_BASE_HEADER, "feeAmount", "taxAmount")
_STAGE_COLUMNS = (
    "provider_item_id",
    "stable_key",
    "operation_kind",
    "amount",
    "currency",
    "status",
    "occurred_at",
    "fee_amount",
    "tax_amount",
)


def _invalid(message: str) -> RelayPayError:
    return RelayPayError(code="INVALID_STATEMENT", message=message, http_status=422)


class StreamedStatementHasher:
    """Spool a request body to a file while hashing it; the caller then reads it back once."""

    def __init__(self, spool: IO[bytes]) -> None:
        self._spool = spool
        self._digest = hashlib.sha256()
        self.size = 0

    def write(self, block: bytes) -> None:
        self.size += len(block)
        if self.size > MAX_STREAMED_BYTES:
            raise RelayPayError(
                code="REQUEST_BODY_TOO_LARGE",
                message="Statement exceeds the streamed import size limit",
                http_status=413,
                details={"maxBytes": MAX_STREAMED_BYTES},
            )
        self._digest.update(block)
        self._spool.write(block)

    def finish(self) -> bytes:
        self._spool.seek(0)
        return self._digest.digest()


def _read_header(body: IO[bytes]) -> tuple[str, ...]:
    first = body.readline(64 * 1024)
    if not first.endswith(b"\n") and len(first) >= 64 * 1024:
        raise _invalid("Statement CSV header is too long")
    try:
        row = next(csv.reader([first.decode("utf-8").rstrip("\r\n")], strict=True))
    except (UnicodeDecodeError, csv.Error, StopIteration) as error:
        raise _invalid("Statement CSV header is malformed") from error
    header = tuple(row)
    if header not in (_BASE_HEADER, _FEE_HEADER):
        raise _invalid("Statement CSV headers do not match the supported schema")
    return header


_ISO_TIMESTAMP = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$"
)
_VALIDATE = text(
    """
    SELECT ordinal, reason FROM (
      SELECT ordinal, CASE
        WHEN provider_item_id IS NULL OR length(provider_item_id) > 128 THEN 'providerItemId'
        WHEN stable_key IS NULL OR length(stable_key) > 128 THEN 'stableKey'
        WHEN operation_kind IS NULL
             OR operation_kind NOT IN ('AUTHORIZE', 'CAPTURE', 'REFUND', 'VOID')
             THEN 'operationKind'
        WHEN amount IS NULL OR amount !~ '^[1-9][0-9]{0,17}$' THEN 'amount'
        WHEN currency IS NULL OR currency !~ '^[A-Z]{3}$' THEN 'currency'
        WHEN status IS NULL OR status NOT IN ('PENDING', 'SUCCEEDED', 'DECLINED') THEN 'status'
        WHEN occurred_at IS NULL
             OR occurred_at !~ :iso_timestamp_pattern
             THEN 'occurredAt'
        WHEN fee_amount IS NOT NULL AND fee_amount !~ '^[0-9]{1,18}$' THEN 'feeAmount'
        WHEN tax_amount IS NOT NULL AND tax_amount !~ '^[0-9]{1,18}$' THEN 'taxAmount'
      END AS reason FROM _stmt_stage
    ) checked WHERE reason IS NOT NULL ORDER BY ordinal LIMIT 1
    """
)


def import_streamed_statement(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    provider: str,
    source_reference: str,
    period_start: datetime,
    period_end: datetime,
    body: IO[bytes],
    sha256: bytes,
) -> StatementImportResult:
    """Import a CSV statement of any size without holding it in memory.

    ``body`` is positioned at the start of the file (the header row first) and was hashed to
    ``sha256`` by the caller. The whole import is one transaction: either every row lands with its
    run, or nothing does. Repeating the same ``source_reference`` with the same bytes returns the
    original import; different bytes under a known reference are refused.
    """
    require_organisation_admin(principal)
    environment = session.scalar(
        select(Environment).where(
            Environment.organisation_id == principal.organisation_id,
            Environment.public_id == environment_public_id,
            Environment.status == "ACTIVE",
        )
    )
    if environment is None:
        raise not_found("Environment")
    return _import_streamed(
        session,
        environment=environment,
        principal=principal,
        provider=provider,
        source_reference=source_reference,
        period_start=period_start,
        period_end=period_end,
        body=body,
        sha256=sha256,
    )


def import_streamed_statement_as_system(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID,
    provider: str,
    source_reference: str,
    period_start: datetime,
    period_end: datetime,
    body: IO[bytes],
    sha256: bytes,
) -> StatementImportResult:
    """The scheduled import: the same transaction as the admin one, audited as SYSTEM."""
    environment = session.scalar(
        select(Environment).where(
            Environment.organisation_id == organisation_id,
            Environment.id == environment_id,
            Environment.status == "ACTIVE",
        )
    )
    if environment is None:
        raise not_found("Environment")
    return _import_streamed(
        session,
        environment=environment,
        principal=None,
        provider=provider,
        source_reference=source_reference,
        period_start=period_start,
        period_end=period_end,
        body=body,
        sha256=sha256,
    )


def _import_streamed(
    session: Session,
    *,
    environment: Environment,
    principal: Principal | None,
    provider: str,
    source_reference: str,
    period_start: datetime,
    period_end: datetime,
    body: IO[bytes],
    sha256: bytes,
) -> StatementImportResult:
    organisation_id = environment.organisation_id
    if provider != "PAYMENT_PROVIDER":
        raise _invalid("Statement provider is unsupported")
    if not source_reference or len(source_reference) > 128:
        raise _invalid("Statement source reference is invalid")
    if period_start.tzinfo is None or period_end.tzinfo is None or period_end <= period_start:
        raise _invalid("Statement period is invalid")
    lock_material = f"{organisation_id}:{environment.id}:{provider}:{source_reference}".encode()
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)
            )
        )
    )
    existing = session.scalar(
        select(StatementImport)
        .where(
            StatementImport.organisation_id == organisation_id,
            StatementImport.environment_id == environment.id,
            StatementImport.provider == provider,
            StatementImport.source_reference == source_reference,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.raw_sha256 != sha256:
            raise RelayPayError(
                code="STATEMENT_SOURCE_CONFLICT",
                message="Statement source reference is bound to different bytes",
                http_status=409,
            )
        run = session.scalar(
            select(ReconciliationRun)
            .where(ReconciliationRun.statement_import_id == existing.id)
            .order_by(ReconciliationRun.algorithm_version.desc())
            .limit(1)
        )
        if run is None:
            raise RuntimeError("statement import is missing its reconciliation run")
        return StatementImportResult(existing, run, False)

    header = _read_header(body)
    columns = _STAGE_COLUMNS[: len(header)]
    session.execute(
        text(
            """
            CREATE TEMP TABLE _stmt_stage (
              ordinal bigint GENERATED ALWAYS AS IDENTITY,
              provider_item_id text, stable_key text, operation_kind text, amount text,
              currency text, status text, occurred_at text, fee_amount text, tax_amount text
            ) ON COMMIT DROP
            """
        )
    )
    driver_connection = session.connection().connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("streamed import needs a live driver connection")
    with (
        driver_connection.cursor() as cursor,
        cursor.copy(
            f"COPY _stmt_stage ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv)"
        ) as copy,
    ):
        try:
            while block := body.read(_COPY_BLOCK_BYTES):
                copy.write(block)
        except Exception as error:
            # psycopg raises its own error types for malformed CSV; do not echo row content.
            raise _invalid("Statement CSV is malformed") from error

    total = int(session.scalar(text("SELECT count(*) FROM _stmt_stage")) or 0)
    if total == 0:
        raise _invalid("Statement contains no items")
    if total > MAX_STREAMED_ITEMS:
        raise _invalid("Statement contains too many items")
    bad = session.execute(_VALIDATE, {"iso_timestamp_pattern": _ISO_TIMESTAMP}).first()
    if bad is not None:
        raise _invalid(f"Statement row {bad.ordinal} has an invalid {bad.reason}")
    if session.scalar(
        text("SELECT 1 FROM _stmt_stage GROUP BY provider_item_id HAVING count(*) > 1 LIMIT 1")
    ):
        raise _invalid("Statement provider item identifiers must be unique")

    statement_import = StatementImport(
        public_id=new_public_id("stmt"),
        organisation_id=organisation_id,
        environment_id=environment.id,
        provider=provider,
        source_reference=source_reference,
        source_format="CSV",
        period_start=period_start,
        period_end=period_end,
        raw_bytes=None,
        raw_sha256=sha256,
        streamed=True,
    )
    session.add(statement_import)
    session.flush()
    try:
        outside = session.scalar(
            text(
                "SELECT count(*) FROM _stmt_stage "
                "WHERE occurred_at::timestamptz < :start OR occurred_at::timestamptz >= :end"
            ),
            {"start": period_start, "end": period_end},
        )
        if outside:
            raise _invalid("Statement item occurrence is outside the declared period")
        session.execute(
            text(
                """
                INSERT INTO statement_items (
                  id, organisation_id, environment_id, statement_import_id, ordinal,
                  provider_item_id, stable_key, operation_kind, amount, currency,
                  provider_status, occurred_at, fee_amount, tax_amount
                )
                SELECT gen_random_uuid(), :org, :env, :imp, ordinal,
                       provider_item_id, stable_key, operation_kind, amount::bigint, currency,
                       status, occurred_at::timestamptz, fee_amount::bigint, tax_amount::bigint
                  FROM _stmt_stage ORDER BY ordinal
                """
            ),
            {
                "org": organisation_id,
                "env": environment.id,
                "imp": statement_import.id,
            },
        )
    except DataError as error:
        raise _invalid(
            "Statement contains a value that is not a valid number or timestamp"
        ) from error
    run = ReconciliationRun(
        public_id=new_public_id("rrun"),
        organisation_id=organisation_id,
        environment_id=environment.id,
        statement_import_id=statement_import.id,
        algorithm_version=STREAM_ALGORITHM_VERSION,
        status="PENDING",
        attempt_count=0,
    )
    session.add(run)
    audit_details: dict[str, object] = {
        "algorithmVersion": STREAM_ALGORITHM_VERSION,
        "format": "CSV",
        "itemCount": total,
        "provider": provider,
        "sha256": sha256.hex(),
        "sourceReference": source_reference,
        "streamed": True,
    }
    if principal is not None:
        append_audit(
            session,
            principal=principal,
            environment_id=environment.id,
            action="STATEMENT_IMPORTED",
            target_type="STATEMENT_IMPORT",
            target_id=statement_import.public_id,
            details=audit_details,
        )
    else:
        session.add(
            AuditRecord(
                public_id=new_public_id("aud"),
                organisation_id=organisation_id,
                environment_id=environment.id,
                actor_type="SYSTEM",
                actor_id=None,
                action="STATEMENT_IMPORTED",
                target_type="STATEMENT_IMPORT",
                target_id=statement_import.public_id,
                details=audit_details,
            )
        )
    session.flush()
    return StatementImportResult(statement_import, run, True)


# --------------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------------

_PROVIDER_STATUS = (
    "CASE {column} WHEN 'SUCCEEDED' THEN 'SUCCEEDED' WHEN 'FAILED' THEN 'DECLINED' "
    "ELSE 'PENDING' END"
)

# One row per child resource of a provider operation, so a statement row (or an operation) can be
# joined to "what RelayPay recorded" without caring which of four tables holds it.
_INTERNAL_EFFECT = """
    LEFT JOIN LATERAL (
      SELECT a.public_id AS resource_public_id, a.amount, a.currency, a.status,
             NULL::uuid AS journal_id, NULL::bigint AS fee_amount, NULL::bigint AS gst_amount
        FROM authorizations a WHERE o.kind = 'AUTHORIZE' AND a.provider_operation_id = o.id
      UNION ALL
      SELECT cp.public_id, cp.amount, cp.currency, cp.status, cp.journal_id,
             cp.fee_amount, cp.gst_amount
        FROM captures cp WHERE o.kind = 'CAPTURE' AND cp.provider_operation_id = o.id
      UNION ALL
      SELECT v.public_id, v.amount, v.currency, v.status, NULL, NULL, NULL
        FROM voids v WHERE o.kind = 'VOID' AND v.provider_operation_id = o.id
      UNION ALL
      SELECT r.public_id, r.amount, r.currency, r.status, r.journal_id, NULL, NULL
        FROM refunds r WHERE o.kind = 'REFUND' AND r.provider_operation_id = o.id
    ) e ON true
"""

# CREATE TABLE AS is a utility statement, so it cannot take bind parameters; the few values are
# typed (a UUID, ints and a float) and substituted as literals by the helpers below.
_CLASSIFY_TEMPLATE = f"""
    CREATE TEMP TABLE _recon_classified ON COMMIT DROP AS
    WITH joined AS (
      SELECT c.id AS item_id, c.ordinal, c.provider_item_id, c.stable_key,
             c.operation_kind, c.amount, c.currency, c.provider_status, c.occurred_at,
             c.fee_amount AS stmt_fee, c.tax_amount AS stmt_tax,
             o.id AS op_id, o.public_id AS op_public_id, o.kind AS op_kind,
             o.status AS op_status, o.created_at AS op_created,
             e.resource_public_id, e.amount AS int_amount, e.currency AS int_currency,
             e.status AS int_status, e.journal_id, e.fee_amount AS int_fee,
             e.gst_amount AS int_tax,
             EXISTS (
               SELECT 1 FROM statement_items d
                WHERE d.statement_import_id = c.statement_import_id
                  AND d.stable_key = c.stable_key AND d.id <> c.id
             ) AS dup
        FROM _recon_chunk c
        LEFT JOIN provider_operations o
          ON o.organisation_id = c.organisation_id AND o.environment_id = c.environment_id
         AND o.stable_provider_key = c.stable_key
        {_INTERNAL_EFFECT}
    )
    SELECT j.*, CASE
      WHEN j.dup THEN ARRAY['DUPLICATE_PROVIDER_EFFECT']
      WHEN j.op_id IS NULL OR j.resource_public_id IS NULL OR j.op_kind <> j.operation_kind
        THEN ARRAY['MISSING_INTERNAL_TRANSACTION']
      ELSE array_remove(ARRAY[
        CASE WHEN j.amount <> j.int_amount THEN 'AMOUNT_MISMATCH' END,
        CASE WHEN j.currency <> j.int_currency THEN 'CURRENCY_MISMATCH' END,
        CASE WHEN j.provider_status <> {_PROVIDER_STATUS.format(column="j.int_status")}
                OR j.provider_status <> {_PROVIDER_STATUS.format(column="j.op_status")}
             THEN 'STATUS_MISMATCH' END,
        CASE WHEN j.provider_status = 'SUCCEEDED' AND j.operation_kind IN ('CAPTURE', 'REFUND')
                  AND j.journal_id IS NULL THEN 'MISSING_INTERNAL_JOURNAL' END,
        CASE WHEN j.stmt_fee IS NOT NULL AND j.int_fee IS NOT NULL AND j.stmt_fee <> j.int_fee
             THEN 'FEE_MISMATCH' END,
        CASE WHEN j.stmt_tax IS NOT NULL AND j.int_tax IS NOT NULL AND j.stmt_tax <> j.int_tax
             THEN 'TAX_MISMATCH' END,
        CASE WHEN abs(extract(epoch FROM (j.occurred_at - j.op_created))) > :timing_seconds
             THEN 'TIMING' END
      ]::text[], NULL)
    END AS types
    FROM joined j
    """  # noqa: S608 - interpolates only module constants


# The chunk is staged in its own temporary table so it can be ANALYZEd: statement_items was just
# bulk-inserted, the application role cannot ANALYZE a table it does not own, and autovacuum may not
# have reached it. A session may analyze its own temporary table, which gives the planner the real
# row count and key distribution for the join to provider_operations.
_STAGE_CHUNK_TEMPLATE = """
    CREATE TEMP TABLE _recon_chunk ON COMMIT DROP AS
    SELECT * FROM statement_items
     WHERE statement_import_id = :import_id AND ordinal > :lo AND ordinal <= :hi
"""


def _stage_chunk_sql(import_id: uuid.UUID, lo: int, hi: int) -> TextClause:
    return text(
        _STAGE_CHUNK_TEMPLATE.replace(":import_id", f"'{uuid.UUID(str(import_id))}'::uuid")
        .replace(":lo", str(int(lo)))
        .replace(":hi", str(int(hi)))
    )


def _classify_sql(timing_seconds: float) -> TextClause:
    return text(_CLASSIFY_TEMPLATE.replace(":timing_seconds", repr(float(timing_seconds))))


_INSERT_MATCHES = text(
    """
    INSERT INTO reconciliation_matches (
      id, organisation_id, environment_id, reconciliation_run_id, statement_item_id,
      provider_operation_id, journal_id, match_type, evidence
    )
    SELECT gen_random_uuid(), :org, :env, :run, item_id, op_id, journal_id,
           CASE WHEN provider_status = 'DECLINED' THEN 'DECLINED_WITHOUT_JOURNAL' ELSE 'EXACT' END,
           jsonb_build_object(
             'algorithmVersion', 2, 'reason', 'statement and internal evidence match'
           )
      FROM _recon_classified WHERE cardinality(types) = 0
    ON CONFLICT (reconciliation_run_id, statement_item_id) DO NOTHING
    """
)

_EXCEPTIONS = text("SELECT * FROM _recon_classified WHERE cardinality(types) > 0 ORDER BY ordinal")

_MISSING_PROVIDER = text(
    f"""
    SELECT o.id AS op_id, o.public_id AS op_public_id, o.kind AS op_kind, o.status AS op_status,
           o.stable_provider_key, e.resource_public_id, e.amount AS int_amount,
           e.currency AS int_currency, e.status AS int_status, e.journal_id
      FROM provider_operations o
      {_INTERNAL_EFFECT}
     WHERE o.organisation_id = :org AND o.environment_id = :env
       AND o.created_at >= :period_start AND o.created_at < :period_end
       AND NOT EXISTS (
         SELECT 1 FROM _recon_statement_keys s WHERE s.stable_key = o.stable_provider_key
       )
       AND NOT EXISTS (
         SELECT 1 FROM reconciliation_mismatches m
          WHERE m.reconciliation_run_id = :run
            AND m.subject_key = 'operation:' || o.public_id || :missing_suffix
       )
     ORDER BY o.id LIMIT :limit
    """  # noqa: S608 - interpolates only a module constant
)

_REASONS = {
    "DUPLICATE_PROVIDER_EFFECT": "statement contains the stable provider key more than once",
    "MISSING_INTERNAL_TRANSACTION": (
        "no matching internal provider operation and payment child were found"
    ),
    "AMOUNT_MISMATCH": "amounts differ",
    "CURRENCY_MISMATCH": "currencies differ",
    "STATUS_MISMATCH": "provider and internal statuses differ",
    "MISSING_INTERNAL_JOURNAL": "successful money movement has no internal journal",
    "FEE_MISMATCH": "provider fee differs from the fee RelayPay charged",
    "TAX_MISMATCH": "provider tax differs from the GST RelayPay charged",
    "TIMING": "statement time is more than a day from when RelayPay recorded the operation",
}


def _evidence_document(
    row: Any, *, reason: str, with_statement_item: bool, with_internal: bool
) -> dict[str, object]:
    """Build the same evidence shape algorithm 1 writes, from one classified row."""
    internal: dict[str, object] | None = None
    if with_internal and row.resource_public_id is not None:
        internal = {
            "amount": row.int_amount,
            "currency": row.int_currency,
            "journalId": str(row.journal_id) if row.journal_id else None,
            "resourceId": row.resource_public_id,
            "status": row.int_status,
        }
    operation: dict[str, object] | None = None
    if row.op_id is not None:
        operation = {
            "id": row.op_public_id,
            "kind": row.op_kind,
            "stableKey": row.stable_key if with_statement_item else row.stable_provider_key,
            "status": row.op_status,
        }
    statement_item: dict[str, object] | None = None
    if with_statement_item:
        statement_item = {
            "amount": row.amount,
            "currency": row.currency,
            "id": row.provider_item_id,
            "kind": row.operation_kind,
            "stableKey": row.stable_key,
            "status": row.provider_status,
        }
        if row.stmt_fee is not None:
            statement_item["feeAmount"] = row.stmt_fee
        if row.stmt_tax is not None:
            statement_item["taxAmount"] = row.stmt_tax
        # What RelayPay charged is evidence only when the statement made a fee claim to compare.
        if (
            internal is not None
            and row.int_fee is not None
            and (row.stmt_fee is not None or row.stmt_tax is not None)
        ):
            internal["feeAmount"] = row.int_fee
            internal["gstAmount"] = row.int_tax
    return {
        "internal": internal,
        "providerOperation": operation,
        "reason": reason,
        "statementItem": statement_item,
    }


def _record_mismatches(
    session: Session, run: ReconciliationRun, entries: Sequence[dict[str, Any]]
) -> int:
    """Bulk-insert mismatches with their first evidence version and OPEN history row."""
    if not entries:
        return 0
    mismatches: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    for entry in entries:
        mismatch_id = new_uuid()
        evidence = entry["evidence"]
        mismatches.append(
            {
                "id": mismatch_id,
                "public_id": new_public_id("mis"),
                "organisation_id": run.organisation_id,
                "environment_id": run.environment_id,
                "reconciliation_run_id": run.id,
                "subject_key": entry["subject_key"],
                "mismatch_type": entry["mismatch_type"],
                "statement_item_id": entry["statement_item_id"],
                "provider_operation_id": entry["provider_operation_id"],
                "workflow_status": "OPEN",
            }
        )
        evidence_rows.append(
            {
                "organisation_id": run.organisation_id,
                "environment_id": run.environment_id,
                "reconciliation_mismatch_id": mismatch_id,
                "version": 1,
                "evidence": evidence,
                "evidence_sha256": hashlib.sha256(canonical_json_bytes(evidence)).digest(),
            }
        )
        history_rows.append(
            {
                "organisation_id": run.organisation_id,
                "environment_id": run.environment_id,
                "reconciliation_mismatch_id": mismatch_id,
                "from_status": None,
                "to_status": "OPEN",
                "actor_id": None,
                "note": None,
            }
        )
    session.execute(insert(ReconciliationMismatch), mismatches)
    session.execute(insert(MismatchEvidenceVersion), evidence_rows)
    session.execute(insert(MismatchWorkflowHistory), history_rows)
    return len(entries)


def _lock_run(
    session: Session, claim_run_id: uuid.UUID, lease_token: uuid.UUID
) -> ReconciliationRun | None:
    return session.scalar(
        select(ReconciliationRun)
        .where(
            ReconciliationRun.id == claim_run_id,
            ReconciliationRun.status == "RUNNING",
            ReconciliationRun.lease_token == lease_token,
        )
        .with_for_update()
    )


@dataclass(frozen=True, slots=True)
class ChunkResult:
    matched: int
    mismatched: int
    progress_ordinal: int
    done_with_items: bool


def _item_chunk(
    session: Session,
    run: ReconciliationRun,
    *,
    chunk_ordinals: int,
    lease_seconds: int,
    total_items: int,
) -> ChunkResult:
    lo = run.progress_ordinal
    hi = min(lo + chunk_ordinals, total_items)
    session.execute(_stage_chunk_sql(run.statement_import_id, lo, hi))
    session.execute(text("ANALYZE _recon_chunk"))
    session.execute(_classify_sql(TIMING_TOLERANCE.total_seconds()))
    matched = int(
        session.execute(
            _INSERT_MATCHES,
            {"org": run.organisation_id, "env": run.environment_id, "run": run.id},
        ).rowcount  # type: ignore[attr-defined]
        or 0
    )
    entries: list[dict[str, Any]] = []
    for row in session.execute(_EXCEPTIONS):
        for mismatch_type in row.types:
            entries.append(
                {
                    "subject_key": f"statement:{row.provider_item_id}:{mismatch_type}",
                    "mismatch_type": mismatch_type,
                    "statement_item_id": row.item_id,
                    "provider_operation_id": row.op_id,
                    "evidence": _evidence_document(
                        row,
                        reason=_REASONS[mismatch_type],
                        with_statement_item=True,
                        with_internal=True,
                    ),
                }
            )
    mismatched = _record_mismatches(session, run, entries)
    run.progress_ordinal = hi
    run.matched_count += matched
    run.mismatch_count += mismatched
    run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    return ChunkResult(matched, mismatched, hi, hi >= total_items)


def _stage_statement_keys(session: Session, statement_import_id: uuid.UUID) -> None:
    """Copy the statement's keys to an analyzable temp table (see ``_STAGE_CHUNK_TEMPLATE``).

    Without current statistics the planner takes ``statement_items`` for a one-row table and
    chooses a nested-loop anti join, which compares every operation to every statement row.
    """
    import_id = uuid.UUID(str(statement_import_id))
    session.execute(
        text(
            "CREATE TEMP TABLE _recon_statement_keys ON COMMIT DROP AS "  # noqa: S608
            "SELECT stable_key FROM statement_items "
            f"WHERE statement_import_id = '{import_id}'::uuid"
        )
    )
    session.execute(text("ANALYZE _recon_statement_keys"))


def _missing_provider_chunk(
    session: Session,
    run: ReconciliationRun,
    statement_import: StatementImport,
    *,
    chunk_ordinals: int,
    lease_seconds: int,
) -> int:
    _stage_statement_keys(session, statement_import.id)
    rows = list(
        session.execute(
            _MISSING_PROVIDER,
            {
                "org": run.organisation_id,
                "env": run.environment_id,
                "period_start": statement_import.period_start,
                "period_end": statement_import.period_end,
                "run": run.id,
                "missing_suffix": ":MISSING_PROVIDER_TRANSACTION",
                "limit": chunk_ordinals,
            },
        )
    )
    entries = [
        {
            "subject_key": f"operation:{row.op_public_id}:MISSING_PROVIDER_TRANSACTION",
            "mismatch_type": "MISSING_PROVIDER_TRANSACTION",
            "statement_item_id": None,
            "provider_operation_id": row.op_id,
            "evidence": _evidence_document(
                row,
                reason="internal provider operation is absent from the statement period",
                with_statement_item=False,
                with_internal=True,
            ),
        }
        for row in rows
    ]
    recorded = _record_mismatches(session, run, entries)
    run.mismatch_count += recorded
    run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    return len(rows)


def complete_chunked_reconciliation(
    factory: sessionmaker[Session],
    run_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    chunk_ordinals: int = CHUNK_ORDINALS,
    lease_seconds: int = 30,
    stop_after_chunks: int | None = None,
) -> bool:
    """Run (or resume) an algorithm-2 run. Returns False if the lease was lost.

    ``stop_after_chunks`` exists so a test can stop a run part-way and prove it resumes; it
    leaves the run RUNNING with its lease intact, exactly as a crashed worker would.
    """
    with factory() as session, session.begin():
        run = _lock_run(session, run_id, lease_token)
        if run is None:
            return False
        total_items = int(
            session.scalar(
                select(func.coalesce(func.max(StatementItem.ordinal), 0)).where(
                    StatementItem.statement_import_id == run.statement_import_id
                )
            )
            or 0
        )
    chunks = 0
    while True:
        with factory() as session, session.begin():
            run = _lock_run(session, run_id, lease_token)
            if run is None:
                return False
            if run.progress_ordinal >= total_items:
                break
            _item_chunk(
                session,
                run,
                chunk_ordinals=chunk_ordinals,
                lease_seconds=lease_seconds,
                total_items=total_items,
            )
        chunks += 1
        if stop_after_chunks is not None and chunks >= stop_after_chunks:
            return True
    while True:
        with factory() as session, session.begin():
            run = _lock_run(session, run_id, lease_token)
            if run is None:
                return False
            statement_import = session.get(StatementImport, run.statement_import_id)
            if statement_import is None:
                raise RuntimeError("reconciliation run has no statement import")
            found = _missing_provider_chunk(
                session,
                run,
                statement_import,
                chunk_ordinals=chunk_ordinals,
                lease_seconds=lease_seconds,
            )
            if found < chunk_ordinals:
                run.status = "COMPLETED"
                run.lease_token = None
                run.lease_expires_at = None
                run.completed_at = datetime.now(UTC)
                run.safe_error_code = None
                return True
        chunks += 1
        if stop_after_chunks is not None and chunks >= stop_after_chunks:
            return True
