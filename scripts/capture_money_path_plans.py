"""Capture EXPLAIN (ANALYZE, BUFFERS) plans for the money-path queries at scale (M19 T19.4).

    uv run python -m scripts.capture_money_path_plans --postings 1000000 \
        --output docs/evidence/v2.0.0/money-path-plans.json

Seeds one synthetic tenant (bulk load as the database superuser with triggers and foreign keys
disabled for the fixture only), runs ``ANALYZE``, records the plans, runs ``VACUUM`` and records
them again, noting for each query for each query the plan's node
types, the relations it scanned sequentially, execution time and buffer counts. The queries are the
ones the services run (the source location is recorded with each one). The seeded tenant is removed
afterwards unless ``--keep`` is given. Use a disposable development database.

The tenant is deliberately lopsided: two ledger accounts carry 98% of the postings, so the
balance ``SUM`` is the worst case a hot merchant produces; a second pair of accounts holding 2%
is the typical merchant.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

SUPERUSER_URL = os.environ.get(
    "RELAYPAY_SUPERUSER_URL",
    "postgresql://postgres:postgres_dev_only@localhost:55432/relaypay",
)


def _seed(world: dict[str, str], postings: int) -> dict[str, int]:
    journals = postings // 2
    payments = max(postings // 5, 1)
    refunds = max(payments // 10, 1)
    processing = max(payments // 100, 1)
    params = {**world, "journals": journals, "payments": payments, "refunds": refunds}
    with psycopg.connect(SUPERUSER_URL) as connection:
        connection.execute("SET session_replication_role = replica")
        connection.execute(
            """
            INSERT INTO payment_intents (
              id, public_id, organisation_id, environment_id, customer_id, merchant_account_id,
              merchant_reference, amount, currency, status, status_version, created_at
            )
            SELECT md5('p' || g)::uuid, 'pi_plan_' || g, %(org)s, %(env)s, %(cust)s, %(merchant)s,
                   'order-' || g, 100000, 'INR', 'captured', 2, now() - (g || ' seconds')::interval
              FROM generate_series(1, %(payments)s) g
            """,
            params,
        )
        connection.execute(
            """
            INSERT INTO provider_operations (
              id, public_id, organisation_id, environment_id, payment_intent_id, resource_type,
              resource_id, kind, stable_provider_key, status, provider_request_bytes,
              provider_request_sha256, attempt_count, last_sent_at, next_lookup_at,
              apply_failure_count, terminal_http_status, terminal_response_bytes,
              terminal_response_sha256, finalized_at
            )
            SELECT md5('o' || g)::uuid, 'op_plan_' || g, %(org)s, %(env)s, md5('p' || g)::uuid,
                   'CAPTURE', md5('c' || g)::uuid, 'CAPTURE', 'capture:plan_' || g,
                   CASE WHEN g <= %(processing)s THEN 'PROCESSING' ELSE 'SUCCEEDED' END,
                   'x'::bytea, sha256('x'::bytea), 1, now(),
                   CASE WHEN g <= %(processing)s THEN now() - interval '1 minute' END,
                   0,
                   CASE WHEN g <= %(processing)s THEN NULL ELSE 200 END,
                   CASE WHEN g <= %(processing)s THEN NULL ELSE 'x'::bytea END,
                   CASE WHEN g <= %(processing)s THEN NULL ELSE sha256('x'::bytea) END,
                   CASE WHEN g <= %(processing)s THEN NULL ELSE now() END
              FROM generate_series(1, %(payments)s) g
            """,
            {**params, "processing": processing},
        )
        connection.execute(
            """
            INSERT INTO journals (
              id, public_id, organisation_id, environment_id, merchant_account_id,
              provider_operation_id, journal_type, reference_type, reference_id, currency
            )
            SELECT md5('j' || g)::uuid, 'jrn_plan_' || g, %(org)s, %(env)s, %(merchant)s,
                   CASE WHEN g <= %(payments)s THEN md5('o' || g)::uuid END,
                   'CAPTURE', 'CAPTURE', md5('cap' || g)::uuid, 'INR'
              FROM generate_series(1, %(journals)s) g
            """,
            params,
        )
        connection.execute(
            """
            INSERT INTO postings (
              id, organisation_id, environment_id, journal_id, account_id, side, amount, currency
            )
            SELECT md5('post' || g || side)::uuid, %(org)s, %(env)s, md5('j' || g)::uuid,
                   CASE WHEN g %% 50 = 0
                        THEN (CASE side WHEN 'DEBIT' THEN %(small_debit)s::uuid
                                        ELSE %(small_credit)s::uuid END)
                        ELSE (CASE side WHEN 'DEBIT' THEN %(debit_account)s::uuid
                                        ELSE %(credit_account)s::uuid END)
                   END,
                   side, 100000, 'INR'
              FROM generate_series(1, %(journals)s) g, (VALUES ('DEBIT'), ('CREDIT')) s(side)
            """,
            params,
        )
        connection.execute(
            """
            INSERT INTO captures (
              id, public_id, organisation_id, environment_id, payment_intent_id,
              authorization_id, provider_operation_id, journal_id, amount, currency, status,
              captured_at, fee_amount, gst_amount
            )
            SELECT md5('c' || g)::uuid, 'cap_plan_' || g, %(org)s, %(env)s, md5('p' || g)::uuid,
                   md5('a' || g)::uuid, md5('o' || g)::uuid, md5('j' || g)::uuid, 100000, 'INR',
                   'SUCCEEDED', now(), 0, 0
              FROM generate_series(1, %(payments)s) g
            """,
            params,
        )
        connection.execute(
            """
            INSERT INTO refunds (
              id, public_id, organisation_id, environment_id, payment_intent_id, capture_id,
              provider_operation_id, journal_id, amount, currency, status, refunded_at
            )
            SELECT md5('r' || g)::uuid, 'ref_plan_' || g, %(org)s, %(env)s, md5('p' || g)::uuid,
                   md5('c' || g)::uuid, md5('ro' || g)::uuid, md5('rj' || g)::uuid, 1000, 'INR',
                   'SUCCEEDED', now()
              FROM generate_series(1, %(refunds)s) g
            """,
            params,
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
              id, organisation_id, environment_id, key_digest, key_hint, fingerprint_sha256,
              fingerprint_summary, target_type, target_id, is_terminal, http_status,
              response_bytes, response_sha256, finalized_at
            )
            SELECT md5('i' || g)::uuid, %(org)s, %(env)s, sha256(('key-' || g)::bytea), 'key',
                   sha256('fp'::bytea), '{}'::jsonb, 'PAYMENT_INTENT', md5('p' || g)::uuid, true,
                   200, 'x'::bytea, sha256('x'::bytea), now()
              FROM generate_series(1, %(payments)s) g
            """,
            params,
        )
        for table in (
            "payment_intents",
            "provider_operations",
            "journals",
            "postings",
            "captures",
            "refunds",
            "idempotency_records",
        ):
            connection.execute(f"ANALYZE {table}")
        connection.commit()
    return {
        "postings": journals * 2,
        "journals": journals,
        "payments": payments,
        "captures": payments,
        "refunds": refunds,
        "idempotencyRecords": payments,
        "processingOperations": processing,
    }


def _walk(plan: dict[str, Any], visit: Any) -> None:
    visit(plan)
    for child in plan.get("Plans", []):
        _walk(child, visit)


def _summarise(explain: list[dict[str, Any]]) -> dict[str, Any]:
    root = explain[0]["Plan"]
    nodes: list[str] = []
    sequential: list[str] = []
    indexes: list[str] = []

    def visit(node: dict[str, Any]) -> None:
        nodes.append(node["Node Type"])
        if node["Node Type"] == "Seq Scan" or node["Node Type"] == "Parallel Seq Scan":
            sequential.append(node.get("Relation Name", "?"))
        if "Index Name" in node:
            indexes.append(node["Index Name"])

    _walk(root, visit)
    return {
        "executionMs": round(explain[0]["Execution Time"], 3),
        "planningMs": round(explain[0]["Planning Time"], 3),
        "rootNode": root["Node Type"],
        "nodes": sorted(set(nodes)),
        "sequentialScans": sorted(set(sequential)),
        "indexesUsed": sorted(set(indexes)),
        "sharedHitBlocks": root.get("Shared Hit Blocks"),
        "sharedReadBlocks": root.get("Shared Read Blocks"),
        "actualRows": root.get("Actual Rows"),
    }


def _queries(world: dict[str, str]) -> list[tuple[str, str, str, dict[str, Any]]]:
    org, env = world["org"], world["env"]
    return [
        (
            "balance_sum",
            "merchant_balances/service.py::_account_balance (derive_balances, 3 per call)",
            "SELECT coalesce(sum(CASE side WHEN 'CREDIT' THEN amount ELSE -amount END), 0) "
            "FROM postings WHERE account_id = %(account)s::uuid",
            {"account": world["credit_account"]},
        ),
        (
            "balance_sum_selective_account",
            "the same query for an account holding 2% of the postings (a typical merchant)",
            "SELECT coalesce(sum(CASE side WHEN 'CREDIT' THEN amount ELSE -amount END), 0) "
            "FROM postings WHERE account_id = %(account)s::uuid",
            {"account": world["small_credit"]},
        ),
        (
            "locked_payment",
            "payments/service.py::_locked_payment (every payment command)",
            "SELECT * FROM payment_intents WHERE organisation_id = %(org)s::uuid "
            "AND environment_id = %(env)s::uuid AND public_id = 'pi_plan_77777' FOR UPDATE",
            {"org": org, "env": env},
        ),
        (
            "refund_trigger_capture_lookup",
            "relaypay_validate_refund_reservations: the capture row",
            "SELECT amount, status FROM captures WHERE id = md5('c777')::uuid "
            "AND organisation_id = %(org)s::uuid",
            {"org": org},
        ),
        (
            "refund_trigger_reserved_sum",
            "relaypay_validate_refund_reservations: refunds reserved against one payment",
            "SELECT coalesce(sum(amount), 0) FROM refunds "
            "WHERE payment_intent_id = md5('p777')::uuid AND organisation_id = %(org)s::uuid "
            "AND status IN ('PROCESSING', 'REQUIRES_REVIEW', 'SUCCEEDED')",
            {"org": org},
        ),
        (
            "idempotency_lookup",
            "payments/service.py idempotency lookup (every command)",
            "SELECT * FROM idempotency_records WHERE organisation_id = %(org)s::uuid "
            "AND environment_id = %(env)s::uuid AND key_digest = sha256('key-77777'::bytea)",
            {"org": org, "env": env},
        ),
        (
            "recovery_claim",
            "provider_operations/recovery.py::claim_due_operations (poller, every second)",
            "SELECT * FROM provider_operations WHERE status = 'PROCESSING' "
            "AND last_sent_at IS NOT NULL AND next_lookup_at <= now() "
            "AND (lookup_lease_expires_at IS NULL OR lookup_lease_expires_at <= now()) "
            "ORDER BY next_lookup_at, id LIMIT 20 FOR UPDATE SKIP LOCKED",
            {},
        ),
        (
            "payments_list_first_page",
            "operations/service.py payments resource: newest page, keyset order",
            "SELECT id, public_id, status, created_at FROM payment_intents "
            "WHERE organisation_id = %(org)s::uuid AND environment_id = %(env)s::uuid "
            "ORDER BY created_at DESC, id DESC LIMIT 51",
            {"org": org, "env": env},
        ),
        (
            "payments_list_deep_page_with_status",
            "operations/service.py payments resource: status filter, cursor deep in the set",
            "SELECT id, public_id, status, created_at FROM payment_intents "
            "WHERE organisation_id = %(org)s::uuid AND environment_id = %(env)s::uuid "
            "AND status = 'captured' AND (created_at, id) < (now() - interval '100000 seconds', "
            "'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid) "
            "ORDER BY created_at DESC, id DESC LIMIT 51",
            {"org": org, "env": env},
        ),
        (
            "gcra_admit",
            "identity/rate_limit.py::GcraLimiter.admit (every API-key request)",
            "INSERT INTO api_rate_limit_state AS s (key, tat) "
            "VALUES ('plan:key', clock_timestamp() + make_interval(secs => 0.01)) "
            "ON CONFLICT (key) DO UPDATE SET tat = GREATEST(s.tat, clock_timestamp()) "
            "+ make_interval(secs => 0.01) "
            "WHERE s.tat <= clock_timestamp() + make_interval(secs => 1.0) RETURNING 1",
            {},
        ),
    ]


def _capture(world: dict[str, str]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with psycopg.connect(SUPERUSER_URL) as connection:
        for name, source, sql, params in _queries(world):
            # EXPLAIN ANALYZE executes the statement; roll back so FOR UPDATE and the GCRA
            # insert leave nothing behind. Run twice and record the second (warm) run.
            runs = []
            for _ in range(2):
                # EXPLAIN is a utility statement, so its parameters must be bound client-side.
                with psycopg.ClientCursor(connection) as cursor:
                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
                    row = cursor.fetchone()
                connection.rollback()
                assert row is not None
                runs.append(_summarise(row[0]))
            results[name] = {"source": source, "cold": runs[0], "warm": runs[1]}
    return results


def _vacuum() -> None:
    with psycopg.connect(SUPERUSER_URL, autocommit=True) as connection:
        for table in ("postings", "journals", "payment_intents", "captures", "refunds"):
            connection.execute(f"VACUUM (ANALYZE) {table}")


def _cleanup(world: dict[str, str]) -> None:
    with psycopg.connect(SUPERUSER_URL) as connection:
        connection.execute("SET session_replication_role = replica")
        for table in (
            "idempotency_records",
            "refunds",
            "captures",
            "postings",
            "journals",
            "provider_operations",
            "payment_intents",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE organisation_id = %s",  # noqa: S608 - fixed allowlist
                (world["org"],),
            )
        connection.commit()
    with psycopg.connect(SUPERUSER_URL, autocommit=True) as connection:
        # Return the fixture's space; otherwise later runs scan megabytes of nothing.
        for table in (
            "idempotency_records",
            "refunds",
            "captures",
            "postings",
            "journals",
            "provider_operations",
            "payment_intents",
        ):
            connection.execute(f"VACUUM FULL ANALYZE {table}")


def _world() -> dict[str, str]:
    from tests.support.db import relaypay_factory
    from tests.support.fake_provider import FakeProvider
    from tests.support.world import balances, build_world, paid_payment

    with relaypay_factory("plan-proof", pool_size=2) as factory:
        world = build_world(factory, label="plan-proof")
        paid_payment(factory, world, FakeProvider())  # creates the merchant's ledger accounts
        balances(factory, world)  # derive_balances creates the remaining accounts on demand
    with psycopg.connect(SUPERUSER_URL) as connection:
        customer = connection.execute(
            "SELECT id FROM customers WHERE organisation_id = %s LIMIT 1",
            (world.organisation_id,),
        ).fetchone()
        accounts = connection.execute(
            "SELECT code, id FROM ledger_accounts WHERE organisation_id = %s",
            (world.organisation_id,),
        ).fetchall()
    by_code = {code: str(account_id) for code, account_id in accounts}
    assert customer is not None
    return {
        "org": str(world.organisation_id),
        "env": str(world.environment_id),
        "cust": str(customer[0]),
        "merchant": str(world.merchant_account_id),
        "debit_account": by_code["PROVIDER_CLEARING_ASSET"],
        "credit_account": by_code["PENDING_PAYABLE_LIABILITY"],
        "small_debit": by_code["MERCHANT_RECEIVABLE_ASSET"],
        "small_credit": by_code["AVAILABLE_PAYABLE_LIABILITY"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--postings", type=int, default=1_000_000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    world = _world()
    started = time.perf_counter()
    sizes = _seed(world, args.postings)
    seed_seconds = time.perf_counter() - started
    try:
        # "Freshly loaded": no visibility map yet, so index-only scans look expensive to the
        # planner. "After VACUUM" is the steady state autovacuum reaches on a live table.
        fresh = _capture(world)
        _vacuum()
        plans = _capture(world)
    finally:
        if not args.keep:
            _cleanup(world)
    document = {
        "benchmark": "money-path-query-plans",
        "recordedAt": datetime.now(UTC).isoformat(),
        "datasetRows": sizes,
        "fixtureSeedSeconds": round(seed_seconds, 1),
        "queries": plans,
        "queriesFreshlyLoaded": fresh,
        "sequentialScansAfterVacuum": {
            name: entry["warm"]["sequentialScans"]
            for name, entry in plans.items()
            if entry["warm"]["sequentialScans"]
        },
        "sequentialScansFreshlyLoaded": {
            name: entry["warm"]["sequentialScans"]
            for name, entry in fresh.items()
            if entry["warm"]["sequentialScans"]
        },
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
        },
        "note": "One machine, one Postgres in Docker; warm-cache run of each plan. The tenant "
        "is lopsided on purpose: two ledger accounts carry 98% of the postings.",
    }
    text = json.dumps(document, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
