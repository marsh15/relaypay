"""Daily automatic statement import (M18 T18.2).

Once a day the poller asks the provider for the previous UTC day's statement and imports it, with
a SYSTEM actor, into every environment that recorded provider operations that day. The
``source_reference`` is ``auto:{date}``, so the import is idempotent: a second tick, a second
poller, or a restart finds the existing import and does nothing. Each environment is imported in
its own transaction, so one failing environment does not hold back the rest.

The provider here exports one statement for its whole account. When several tenants share that
account, each tenant's import contains the others' items, which reconcile as
``MISSING_INTERNAL_TRANSACTION``. That is a property of the sandbox's single shared provider
account, not of the matching, and is why the schedule is off unless
``AUTO_STATEMENT_IMPORT_ENABLED`` is set.
"""

import csv
import hashlib
import io
import json
import logging
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.identity.models import Environment
from relaypay.provider_operations.models import ProviderOperation
from relaypay.reconciliation.models import StatementImport
from relaypay.reconciliation.scale import import_streamed_statement_as_system

logger = logging.getLogger(__name__)

AUTO_SOURCE_PREFIX = "auto:"
_HEADER = (
    "providerItemId",
    "stableKey",
    "operationKind",
    "amount",
    "currency",
    "status",
    "occurredAt",
)

# source_reference, period start, period end -> the provider's statement as JSON bytes.
StatementFetcher = Callable[[str, datetime, datetime], bytes]


def previous_utc_day(now: datetime) -> tuple[date, datetime, datetime]:
    day = (now.astimezone(UTC) - timedelta(days=1)).date()
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return day, start, start + timedelta(days=1)


def json_statement_to_csv(raw: bytes) -> bytes:
    """Re-encode the provider's JSON export as the CSV the streamed import takes."""
    document = json.loads(raw)
    items = document["items"] if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise RelayPayError(
            code="INVALID_STATEMENT", message="Provider statement has no items", http_status=422
        )
    # Fee and tax columns are present only when the provider's export carries them.
    with_fees = any("feeAmount" in item or "taxAmount" in item for item in items)
    columns = (*_HEADER, "feeAmount", "taxAmount") if with_fees else _HEADER
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for item in items:
        writer.writerow(["" if item.get(column) is None else item[column] for column in columns])
    return out.getvalue().encode()


def _active_environments(
    session: Session, start: datetime, end: datetime
) -> list[tuple[object, object]]:
    return [
        (row.organisation_id, row.id)
        for row in session.execute(
            select(Environment.organisation_id, Environment.id)
            .where(
                Environment.status == "ACTIVE",
                Environment.id.in_(
                    select(ProviderOperation.environment_id).where(
                        ProviderOperation.created_at >= start, ProviderOperation.created_at < end
                    )
                ),
            )
            .order_by(Environment.id)
        )
    ]


def run_daily_statement_import(
    factory: sessionmaker[Session],
    *,
    fetch: StatementFetcher,
    now: datetime | None = None,
) -> int:
    """Import yesterday's statement where it has not been imported yet. Returns imports created."""
    day, start, end = previous_utc_day(now or datetime.now(UTC))
    source_reference = f"{AUTO_SOURCE_PREFIX}{day.isoformat()}"
    with factory() as session, session.begin():
        targets = _active_environments(session, start, end)
        done = {
            row
            for row in session.scalars(
                select(StatementImport.environment_id).where(
                    StatementImport.source_reference == source_reference
                )
            )
        }
    pending = [(org, env) for org, env in targets if env not in done]
    if not pending:
        return 0
    csv_bytes = json_statement_to_csv(fetch(source_reference, start, end))
    digest = hashlib.sha256(csv_bytes).digest()
    created = 0
    for organisation_id, environment_id in pending:
        try:
            with (
                tempfile.SpooledTemporaryFile() as body,
                factory() as session,
                session.begin(),
            ):
                body.write(csv_bytes)
                body.seek(0)
                result = import_streamed_statement_as_system(
                    session,
                    organisation_id=organisation_id,  # type: ignore[arg-type]
                    environment_id=environment_id,  # type: ignore[arg-type]
                    provider="PAYMENT_PROVIDER",
                    source_reference=source_reference,
                    period_start=start,
                    period_end=end,
                    body=body,
                    sha256=digest,
                )
                created += int(result.created)
        except Exception:
            logger.exception("daily statement import failed for environment %s", environment_id)
    return created
