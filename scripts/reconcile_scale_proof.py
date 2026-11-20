"""Measure in-database reconciliation at scale and record what it found against what was seeded.

    uv run python -m scripts.reconcile_scale_proof --lines 1000000 \
        --output docs/evidence/v2.0.0/reconcile-in-db.json

The proof builds one synthetic tenant with ``--lines`` authorizations, writes a provider
statement CSV for them with a known set of seeded exceptions, then runs the real algorithm-2 path
(streamed COPY import, chunked matching) in a *child process* so that the child's peak resident
memory is the application's, not this script's. It records import and matching time, the child's
peak RSS, and, per exception type, how many were seeded and how many were found. The run fails if
any count differs, so a faster-but-wrong result cannot be recorded.

Bulk seeding needs the database superuser (it disables triggers and foreign keys for the fixture
load only; every row satisfies the CHECK constraints). The seeded tenant is deleted afterwards
unless ``--keep`` is given. This is a measurement tool for a disposable development database,
never for a shared one. Disk needed is roughly 2 KB per line.

Seeded exceptions (each on a different residue, so none overlap): 1 operation in 500 is left off
the statement; 1 in 1,000 has the wrong amount; 1 in 2,000 also has a statement row for a stable
key RelayPay never recorded; 1 in 5,000 appears twice on the statement.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

SUPERUSER_URL = os.environ.get(
    "RELAYPAY_SUPERUSER_URL",
    "postgresql://postgres:postgres_dev_only@localhost:55432/relaypay",
)
HEADER = [
    "providerItemId",
    "stableKey",
    "operationKind",
    "amount",
    "currency",
    "status",
    "occurredAt",
]
WRONG_AMOUNT_EVERY = 1_000
MISSING_FROM_STATEMENT_EVERY = 500
UNKNOWN_EVERY = 2_000
DUPLICATE_EVERY = 5_000


def _amount(index: int) -> int:
    return 100_000 + (index % 7) * 100


def _seed(world: dict[str, Any], lines: int, when: datetime) -> None:
    """Bulk-insert ``lines`` payment intents, authorize operations and authorizations."""
    with psycopg.connect(SUPERUSER_URL, autocommit=False) as connection:
        connection.execute("SET session_replication_role = replica")
        params = {
            "org": world["organisation_id"],
            "env": world["environment_id"],
            "cust": world["customer_id"],
            "merchant": world["merchant_account_id"],
            "n": lines,
            "when": when,
            "expires": when + timedelta(days=7),
        }
        connection.execute(
            """
            INSERT INTO payment_intents (
              id, public_id, organisation_id, environment_id, customer_id, merchant_account_id,
              merchant_reference, amount, currency, status, status_version
            )
            SELECT md5('pi' || g)::uuid, 'pi_scale_' || g, %(org)s, %(env)s, %(cust)s,
                   %(merchant)s, 'order-' || g, 100000 + (g %% 7) * 100, 'INR', 'authorised', 1
              FROM generate_series(1, %(n)s) g
            """,
            params,
        )
        connection.execute(
            """
            INSERT INTO provider_operations (
              id, public_id, organisation_id, environment_id, payment_intent_id, resource_type,
              resource_id, kind, stable_provider_key, status, provider_request_bytes,
              provider_request_sha256, attempt_count, last_sent_at, apply_failure_count,
              terminal_http_status, terminal_response_bytes, terminal_response_sha256,
              finalized_at, created_at
            )
            SELECT md5('op' || g)::uuid, 'op_scale_' || g, %(org)s, %(env)s, md5('pi' || g)::uuid,
                   'AUTHORIZATION', md5('au' || g)::uuid, 'AUTHORIZE', 'authorize:scale_' || g,
                   'SUCCEEDED', 'x'::bytea, sha256('x'::bytea), 1, %(when)s, 0, 200, 'x'::bytea,
                   sha256('x'::bytea), %(when)s, %(when)s
              FROM generate_series(1, %(n)s) g
            """,
            params,
        )
        connection.execute(
            """
            INSERT INTO authorizations (
              id, public_id, organisation_id, environment_id, payment_intent_id,
              provider_operation_id, amount, currency, status, authorized_at, hold_seconds,
              expires_at
            )
            SELECT md5('au' || g)::uuid, 'auth_scale_' || g, %(org)s, %(env)s, md5('pi' || g)::uuid,
                   md5('op' || g)::uuid, 100000 + (g %% 7) * 100, 'INR', 'SUCCEEDED', %(when)s,
                   604800, %(expires)s
              FROM generate_series(1, %(n)s) g
            """,
            params,
        )
        connection.execute("ANALYZE payment_intents")
        connection.execute("ANALYZE provider_operations")
        connection.execute("ANALYZE authorizations")
        connection.commit()


def _write_statement(path: Path, lines: int, when: datetime) -> dict[str, int]:
    seeded = {
        "AMOUNT_MISMATCH": 0,
        "MISSING_PROVIDER_TRANSACTION": 0,
        "MISSING_INTERNAL_TRANSACTION": 0,
        "DUPLICATE_PROVIDER_EFFECT": 0,
    }
    stamp = when.isoformat()
    rows = 0
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for index in range(1, lines + 1):
            if index % MISSING_FROM_STATEMENT_EVERY == 0:
                seeded["MISSING_PROVIDER_TRANSACTION"] += 1
                continue
            key = f"authorize:scale_{index}"
            if index % DUPLICATE_EVERY == 7:
                # the same effect twice: each of the two rows is a duplicate exception
                for copy in ("a", "b"):
                    rows += 1
                    writer.writerow(
                        [
                            f"item_{index}_{copy}",
                            key,
                            "AUTHORIZE",
                            _amount(index),
                            "INR",
                            "SUCCEEDED",
                            stamp,
                        ]
                    )
                    seeded["DUPLICATE_PROVIDER_EFFECT"] += 1
                continue
            amount = _amount(index) + (1 if index % WRONG_AMOUNT_EVERY == 3 else 0)
            if index % WRONG_AMOUNT_EVERY == 3:
                seeded["AMOUNT_MISMATCH"] += 1
            rows += 1
            writer.writerow([f"item_{index}", key, "AUTHORIZE", amount, "INR", "SUCCEEDED", stamp])
            if index % UNKNOWN_EVERY == 5:
                rows += 1
                writer.writerow(
                    [
                        f"ghost_{index}",
                        f"authorize:ghost_{index}",
                        "AUTHORIZE",
                        5_000,
                        "INR",
                        "SUCCEEDED",
                        stamp,
                    ]
                )
                seeded["MISSING_INTERNAL_TRANSACTION"] += 1
    seeded["_statement_rows"] = rows
    return seeded


# The child process does the measured work: import, then match. Its peak RSS is the application's.
_CHILD = r"""
import hashlib, json, sys, time, tempfile, uuid
from datetime import datetime, timedelta
from relaypay.reconciliation.scale import complete_chunked_reconciliation, import_streamed_statement
from relaypay.reconciliation.service import claim_reconciliation_run
from tests.support.db import relaypay_factory

args = json.loads(sys.argv[1])
import os
if os.environ.get("RELAYPAY_SCALE_TRACE"):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    @event.listens_for(Engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        conn.info["t0"] = time.perf_counter()
    @event.listens_for(Engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        spent = time.perf_counter() - conn.info["t0"]
        if spent > 1.0:
            print(f"slow {spent:.1f}s: {' '.join(statement.split())[:110]}", file=sys.stderr)
from relaypay.identity.security import Principal
principal = Principal(
    kind="SESSION", organisation_id=uuid.UUID(args["org"]),
    organisation_public_id=args["org_public"],
    environment_id=None, environment_public_id=None, display_name="scale proof",
    scopes=frozenset(),
    membership_role="ORGANISATION_ADMIN", user_id=uuid.UUID(args["user"]),
)
when = datetime.fromisoformat(args["when"])
digest = hashlib.sha256()
with open(args["path"], "rb") as handle:
    for block in iter(lambda: handle.read(1 << 20), b""):
        digest.update(block)
with relaypay_factory("scale-proof", pool_size=4) as factory:
    started = time.perf_counter()
    with open(args["path"], "rb") as body, factory() as session, session.begin():
        result = import_streamed_statement(
            session, principal=principal, environment_public_id=args["env_public"],
            provider="PAYMENT_PROVIDER", source_reference="scale-" + uuid.uuid4().hex,
            period_start=when - timedelta(hours=1), period_end=when + timedelta(hours=1),
            body=body, sha256=digest.digest(),
        )
        run_id = result.reconciliation_run.id
    imported = time.perf_counter() - started
    claim = claim_reconciliation_run(factory, run_id=run_id)
    assert claim is not None
    started = time.perf_counter()
    ok = complete_chunked_reconciliation(factory, claim.run_id, claim.lease_token)
    matched = time.perf_counter() - started
print(json.dumps({
    "importSeconds": imported, "matchSeconds": matched, "completed": ok, "runId": str(run_id),
}))
"""


def _build_world() -> dict[str, Any]:
    from tests.support.db import relaypay_factory
    from tests.support.world import build_world

    with relaypay_factory("scale-proof-setup", pool_size=2) as factory:
        world = build_world(factory, label="scale-proof")
        with psycopg.connect(SUPERUSER_URL) as connection:
            row = connection.execute(
                "SELECT id FROM customers WHERE organisation_id = %s LIMIT 1",
                (world.organisation_id,),
            ).fetchone()
    assert row is not None
    return {
        "organisation_id": str(world.organisation_id),
        "organisation_public_id": world.organisation_public_id,
        "environment_id": str(world.environment_id),
        "environment_public_id": world.environment_public_id,
        "merchant_account_id": str(world.merchant_account_id),
        "customer_id": str(row[0]),
        "user_id": str(world.admin_principal.user_id),
    }


def _found(run_id: str) -> dict[str, int]:
    with psycopg.connect(SUPERUSER_URL) as connection:
        rows = connection.execute(
            "SELECT mismatch_type, count(*) FROM reconciliation_mismatches "
            "WHERE reconciliation_run_id = %s GROUP BY 1",
            (run_id,),
        ).fetchall()
        matched = connection.execute(
            "SELECT matched_count, mismatch_count, progress_ordinal FROM reconciliation_runs "
            "WHERE id = %s",
            (run_id,),
        ).fetchone()
    found = {mismatch_type: count for mismatch_type, count in rows}
    assert matched is not None
    found["_matched"], found["_mismatch_rows"], found["_progress_ordinal"] = matched
    return found


def _cleanup(world: dict[str, Any]) -> None:
    """Delete the seeded tenant. Evidence tables reject DELETE, so triggers are lifted here only."""
    with psycopg.connect(SUPERUSER_URL) as connection:
        connection.execute("SET session_replication_role = replica")
        for table in (
            "mismatch_workflow_history",
            "mismatch_evidence_versions",
            "reconciliation_mismatches",
            "reconciliation_matches",
            "reconciliation_runs",
            "statement_items",
            "statement_imports",
            "authorizations",
            "provider_operations",
            "payment_intents",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE organisation_id = %s",  # noqa: S608 - fixed allowlist
                (world["organisation_id"],),
            )
        connection.commit()
    _compact(
        "mismatch_workflow_history",
        "mismatch_evidence_versions",
        "reconciliation_mismatches",
        "reconciliation_matches",
        "reconciliation_runs",
        "statement_items",
        "statement_imports",
        "authorizations",
        "provider_operations",
        "payment_intents",
    )


def _compact(*tables: str) -> None:
    """Return the space the fixture's rows took; otherwise later runs scan megabytes of nothing."""
    with psycopg.connect(SUPERUSER_URL, autocommit=True) as connection:
        for table in tables:
            connection.execute(f"VACUUM FULL ANALYZE {table}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--lines", type=int, default=100_000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep", action="store_true", help="leave the seeded tenant in place")
    args = parser.parse_args(argv)

    when = datetime.now(UTC).replace(microsecond=0)
    world = _build_world()
    started = time.perf_counter()
    _seed(world, args.lines, when)
    seed_seconds = time.perf_counter() - started
    with tempfile.TemporaryDirectory(prefix="relaypay-scale-") as directory:
        statement = Path(directory) / "statement.csv"
        seeded = _write_statement(statement, args.lines, when)
        size = statement.stat().st_size
        payload = json.dumps(
            {
                "org": world["organisation_id"],
                "org_public": world["organisation_public_id"],
                "env_public": world["environment_public_id"],
                "user": world["user_id"],
                "when": when.isoformat(),
                "path": str(statement),
            }
        )
        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        child = subprocess.run(  # noqa: S603 - fixed interpreter and inline module code
            [sys.executable, "-c", _CHILD, payload],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if os.environ.get("RELAYPAY_SCALE_TRACE"):
        print(child.stderr[-4000:], file=sys.stderr)
    if child.returncode != 0:
        print(child.stderr[-3000:], file=sys.stderr)
        _cleanup(world)
        return 2
    timings = json.loads(child.stdout.strip().splitlines()[-1])
    peak_bytes = max(after, before)
    peak_bytes = peak_bytes if platform.system() == "Darwin" else peak_bytes * 1024
    found = _found(timings["runId"])

    comparison = {
        name: {"seeded": seeded[name], "found": found.get(name, 0)}
        for name in (
            "AMOUNT_MISMATCH",
            "MISSING_PROVIDER_TRANSACTION",
            "MISSING_INTERNAL_TRANSACTION",
            "DUPLICATE_PROVIDER_EFFECT",
        )
    }
    exact = all(entry["seeded"] == entry["found"] for entry in comparison.values())
    expected_matched = seeded["_statement_rows"] - sum(
        seeded[name]
        for name in ("AMOUNT_MISMATCH", "MISSING_INTERNAL_TRANSACTION", "DUPLICATE_PROVIDER_EFFECT")
    )
    exact = exact and found["_matched"] == expected_matched
    exact = exact and found["_progress_ordinal"] == seeded["_statement_rows"]

    document = {
        "benchmark": "reconciliation-in-database",
        "lines": args.lines,
        "statementRows": seeded["_statement_rows"],
        "statementBytes": size,
        "importSeconds": round(timings["importSeconds"], 3),
        "matchSeconds": round(timings["matchSeconds"], 3),
        "totalSeconds": round(timings["importSeconds"] + timings["matchSeconds"], 3),
        "childPeakRssBytes": peak_bytes,
        "childPeakRssMiB": round(peak_bytes / 1024 / 1024, 1),
        "matched": {"expected": expected_matched, "found": found["_matched"]},
        "exceptions": comparison,
        "allCountsExact": exact,
        "fixtureSeedSeconds": round(seed_seconds, 3),
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "python": platform.python_version(),
        },
        "note": "One machine, one Postgres in Docker. The child process is the application: its "
        "peak RSS excludes the database server. Rows were seeded in bulk with triggers disabled "
        "for the fixture load only.",
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    if not args.keep:
        _cleanup(world)
    return 0 if exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
