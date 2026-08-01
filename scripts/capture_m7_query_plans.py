"""Capture before/after query-plan evidence against a bounded rollback-only dataset."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from relaypay.config import get_settings
from sqlalchemy import create_engine, text

QUERY = """
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT request_id, status_code, duration_ms
FROM request_logs
ORDER BY created_at DESC, id DESC
LIMIT 100
"""


def capture() -> dict[str, object]:
    settings = get_settings()
    url = (
        settings.RELAYPAY_MIGRATION_DATABASE_URL or settings.RELAYPAY_DATABASE_URL
    ).get_secret_value()
    engine = create_engine(url, connect_args={"application_name": "relaypay-m7-plan-proof"})
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(
                        """
                        INSERT INTO request_logs (
                            request_id, method, route, status_code, duration_ms, id, created_at
                        )
                        SELECT
                            'm7-plan-' || value,
                            'GET',
                            '/bounded-plan-proof',
                            200,
                            value % 50,
                            md5('m7-plan-' || value)::uuid,
                            now() - (value || ' milliseconds')::interval
                        FROM generate_series(1, 5000) AS value
                        """
                    )
                )
                connection.execute(text("DROP INDEX ix_request_logs_created"))
                before = connection.execute(text(QUERY)).scalar_one()
                connection.execute(
                    text("CREATE INDEX ix_request_logs_created ON request_logs (created_at, id)")
                )
                connection.execute(text("ANALYZE request_logs"))
                after = connection.execute(text(QUERY)).scalar_one()
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    return {
        "schemaVersion": 1,
        "recordedAt": datetime.now(UTC).isoformat(),
        "datasetRows": 5000,
        "transactionRolledBack": True,
        "query": "request_logs newest 100",
        "beforeIndex": before[0],
        "afterIndex": after[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    payload = json.dumps(capture(), indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(f"{payload}\n")
    else:
        print(payload)


if __name__ == "__main__":
    main()
