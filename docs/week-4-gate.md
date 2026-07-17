# Week 4 delivery gate

Status: **GO** (2026-07-17)

Scope stopped at Week 4. No Week 5 presentation work is included.

## Implemented contract

- Immutable endpoint versions, merchant events, recipient snapshots, and delivery attempts.
- Recipient selection inside the payment finalizer transaction.
- Crash-safe, idempotent recipient materialization with `FOR UPDATE SKIP LOCKED`.
- Short delivery leases with expiry reclaim, exact-byte HMAC signing, bounded retry, and dead letter.
- Allowlisted bundled receiver destination; redirects are disabled.
- Receiver timestamp/signature validation, byte-digest contradiction detection, and atomic deduplication.
- PostgreSQL-authoritative provider recovery, materialization, and delivery polling; Celery/Redis remains optional acceleration.
- Session-only, tenant-scoped, bounded, redacted payment evidence projection.
- Structured request logs with request IDs and safe route/status/duration fields.
- Liveness and schema-aware readiness probes.
- Machine-checkable lost-response capture CLI.

## Verification evidence

Clean migration cycle:

```text
receiver:  base -> 0001_receiver
relaypay:  base -> 0001_foundation -> 0002_payments -> 0003_events -> 0004_delivery
provider:  base -> 0001_provider
alembic check: no new upgrade operations detected (all three metadata sets)
```

Quality gates:

```text
ruff check: passed
ruff format --check: 86 files formatted
mypy --strict: 61 source files passed
pytest pass 1: 62 passed
pytest pass 2: 62 passed
```

Redis-outage proof: Redis container was stopped before invocation and restored afterward.

```json
{"captures":1,"deliveries":1,"events":1,"idempotencyRecords":2,"journals":1,"ledgerBalanced":true,"providerEffects":1,"receiverRows":1,"recipients":1,"scenario":"capture_lost_response","stableReplay":true}
```

The CLI also emits the immutable event and terminal response SHA-256 digests on every run.

## Reproduce

```bash
make migrate
make seed
make test
make demo
```

For the Redis-independence check, stop only Redis, run `make demo`, verify the JSON invariants, and then restore Redis.
