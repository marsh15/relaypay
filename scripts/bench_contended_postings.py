"""Measure posting throughput on one contended merchant account before and after 0035 (ADR-014).

    uv run python -m scripts.bench_contended_postings \
        --output docs/evidence/v2.0.0/contended-postings-bench.json

Migration 0035 adds a trigger that updates the account's ``ledger_account_balances`` row in the
posting's statement, so the row lock is held until the posting's transaction commits and
concurrent postings to one merchant account run one at a time from that point. This measures what
that costs. In a scratch schema (dropped afterwards) it builds a world through the services, then
for each schema revision (0034: no stored balance; 0035: the trigger) and each thread count:

* ``contended``: every thread posts a two-leg journal (Dr provider clearing, Cr the same
  merchant's PENDING account), the shape of a capture, as fast as it can;
* ``spread``: the same, but each thread credits its own merchant (no shared stored row), the
  control.

``holdMs`` is time spent inside the transaction after the posting and before ``COMMIT``. The
capture finalizer writes balance transactions, status history, events and its idempotency record
after the posting, so a real transaction holds the row lock longer than a bare insert; 0 ms is
the database floor and 2 ms a stand-in for that work. Each run reports transactions per second,
p50 and p95 latency, and errors. After the 0035 runs ``relaypay_ledger_balance_drift()`` must be
empty, or the script exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from alembic import command
from sqlalchemy import create_engine, make_url, select

from scripts.verify_m2_upgrade import (
    RELAYPAY_CONFIG_PATH,
    _base_url,
    _configuration,
    _create_schema,
    _drop_schema,
    _schema_url,
)

BEFORE = "0034_pending_action"
AFTER = "0035_ledger_account_balances"
POST = """
WITH j AS (
  INSERT INTO journals (id, public_id, organisation_id, environment_id, merchant_account_id,
                        journal_type, reference_type, reference_id, currency)
  VALUES (%(journal)s, %(public_id)s, %(org)s, %(env)s, %(merchant)s, 'COMPENSATION',
          'bench_contended_postings', %(journal)s, 'INR')
  RETURNING id
)
INSERT INTO postings (id, organisation_id, environment_id, journal_id, account_id, side, amount,
                      currency)
SELECT gen_random_uuid(), %(org)s, %(env)s, j.id, leg.account_id, leg.side, %(amount)s, 'INR'
  FROM j, (VALUES (%(debit)s::uuid, 'DEBIT'), (%(credit)s::uuid, 'CREDIT')) AS leg(account_id, side)
"""


def _conninfo(url: str) -> str:
    parsed = make_url(url)
    options = parsed.query.get("options")
    return psycopg.conninfo.make_conninfo(
        host=parsed.host or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "",
        password=parsed.password or "",
        dbname=parsed.database or "",
        **({"options": options} if isinstance(options, str) else {}),
    )


def _world(url: str, merchants: int) -> dict[str, Any]:
    """One organisation and ``merchants`` merchant accounts, built through the services."""
    from relaypay.database import build_session_factory
    from relaypay.ledger.models import LedgerAccount
    from relaypay.merchant_balances.service import create_merchant_account

    from tests.support.fake_provider import FakeProvider
    from tests.support.world import build_world, paid_payment

    engine = create_engine(url)
    try:
        factory = build_session_factory(engine)
        world = build_world(factory, label="bench-postings")
        paid_payment(factory, world, FakeProvider())  # a capture: the clearing account exists
        with factory() as session, session.begin():
            for n in range(merchants - 1):
                create_merchant_account(
                    session,
                    organisation_id=world.organisation_id,
                    environment_id=world.environment_id,
                    reference=f"bench-{n}",
                    name=f"Bench {n}",
                    is_default=False,
                )
            session.flush()
            rows = session.execute(
                select(LedgerAccount.code, LedgerAccount.merchant_account_id, LedgerAccount.id)
                .where(LedgerAccount.organisation_id == world.organisation_id)
                .where(LedgerAccount.environment_id == world.environment_id)
            ).all()
    finally:
        engine.dispose()
    pending = sorted(
        (str(merchant), str(account_id))
        for code, merchant, account_id in rows
        if code == "PENDING_PAYABLE_LIABILITY" and merchant is not None
    )
    return {
        "org": str(world.organisation_id),
        "env": str(world.environment_id),
        "clearing": str(
            next(a for c, m, a in rows if c == "PROVIDER_CLEARING_ASSET" and m is None)
        ),
        "pending": pending,  # (merchant id, PENDING account id), default merchant included
    }


def _run(
    conninfo: str,
    world: dict[str, Any],
    *,
    threads: int,
    per_thread: int,
    hold_ms: float,
    contended: bool,
) -> dict[str, Any]:
    connections = [psycopg.connect(conninfo) for _ in range(threads)]
    barrier = threading.Barrier(threads + 1)
    latencies: list[list[float]] = [[] for _ in range(threads)]
    errors: list[str] = []

    def worker(index: int) -> None:
        connection = connections[index]
        merchant, pending = world["pending"][0 if contended else index]
        barrier.wait()
        for _ in range(per_thread):
            journal = uuid.uuid4()
            started = time.perf_counter()
            try:
                with connection.transaction():
                    connection.execute(
                        POST,
                        {
                            "journal": journal,
                            "public_id": f"jrn_bench_{journal.hex}",
                            "org": world["org"],
                            "env": world["env"],
                            "merchant": merchant,
                            "debit": world["clearing"],
                            "credit": pending,
                            "amount": 100,
                        },
                    )
                    if hold_ms:
                        time.sleep(hold_ms / 1000)
            except psycopg.Error as error:
                errors.append(f"{type(error).__name__}: {error}")
                continue
            latencies[index].append((time.perf_counter() - started) * 1000)

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for thread in workers:
        thread.start()
    barrier.wait()
    started = time.perf_counter()
    for thread in workers:
        thread.join()
    wall = time.perf_counter() - started
    for connection in connections:
        connection.close()
    every = sorted(value for values in latencies for value in values)
    committed = len(every)
    return {
        "threads": threads,
        "transactions": committed,
        "errors": len(errors),
        "firstError": errors[0] if errors else None,
        "wallSeconds": round(wall, 3),
        "transactionsPerSecond": round(committed / wall, 1) if wall else None,
        "latencyMsP50": round(statistics.median(every), 3) if every else None,
        "latencyMsP95": round(every[int(0.95 * (committed - 1))], 3) if every else None,
    }


def _drift(conninfo: str) -> int:
    with psycopg.connect(conninfo) as connection:
        row = connection.execute("SELECT count(*) FROM relaypay_ledger_balance_drift()").fetchone()
    return int(row[0]) if row else -1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 4, 16])
    parser.add_argument("--per-thread", type=int, default=300)
    parser.add_argument("--hold-ms", type=float, nargs="+", default=[0.0, 2.0])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    base_url = _base_url(RELAYPAY_CONFIG_PATH, "RELAYPAY_MIGRATION_DATABASE_URL")
    schema = f"bench_postings_{uuid.uuid4().hex}"
    _create_schema(base_url, schema)
    original = os.environ.get("RELAYPAY_MIGRATION_DATABASE_URL")
    results: dict[str, list[dict[str, Any]]] = {}
    drift = -1
    try:
        url = _schema_url(base_url, schema)
        os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = url
        config = _configuration(RELAYPAY_CONFIG_PATH, url)
        command.upgrade(config, "head")
        world = _world(url, max(args.threads))
        conninfo = _conninfo(url)
        for revision in (BEFORE, AFTER):
            if revision == BEFORE:
                command.downgrade(config, BEFORE)
            else:
                command.upgrade(config, AFTER)
            for hold_ms in args.hold_ms:
                for mode in ("contended", "spread"):
                    key = f"{revision}/{mode}/hold{hold_ms:g}ms"
                    results[key] = [
                        _run(
                            conninfo,
                            world,
                            threads=threads,
                            per_thread=args.per_thread,
                            hold_ms=hold_ms,
                            contended=mode == "contended",
                        )
                        for threads in args.threads
                    ]
                    print(key, json.dumps(results[key]))
        drift = _drift(conninfo)
    finally:
        if original is None:
            os.environ.pop("RELAYPAY_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["RELAYPAY_MIGRATION_DATABASE_URL"] = original
        _drop_schema(base_url, schema)

    errors = sum(run["errors"] for runs in results.values() for run in runs)
    document = {
        "benchmark": "contended-merchant-postings",
        "recordedAt": datetime.now(UTC).isoformat(),
        "revisions": {"before": BEFORE, "after": AFTER},
        "perThreadTransactions": args.per_thread,
        "runs": results,
        "storedBalanceDriftAfter": drift,
        "errors": errors,
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
        },
        "note": "One machine, one Postgres in Docker. Each transaction inserts one journal and "
        "two postings in one statement and commits; holdMs sleeps inside the transaction after "
        "the insert. 'contended' credits one shared merchant account, 'spread' one per thread.",
    }
    text = json.dumps(document, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    return 0 if drift == 0 and errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
