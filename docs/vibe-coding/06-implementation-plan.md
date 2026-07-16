# 06 — Six-Week Implementation Plan

## Delivery Strategy

Build one tracer-bullet lifecycle through real PostgreSQL first, then broaden the outcome and concurrency matrix. Correctness precedes console work. Every week ends with observable evidence, not only code completion.

Time budget: 15–20 focused hours per week.

## Week 1 — Foundation, Tenancy, and Ledger

### Goal

Establish reproducible infrastructure, trusted module boundaries, tenant identity, and database-enforced accounting invariants.

### Tasks

1. Initialize Git, Python/Node lockfiles, lint/type/test commands, `.env.example`, and documented developer commands.
2. Create Compose services for two PostgreSQL databases, Redis, API, provider, receiver, workers, scheduler, and console placeholder.
3. Configure separate RelayPay/provider application and migration roles plus an isolated receiver-schema role; prove cross-database and cross-schema application access is denied.
4. Scaffold backend modules and thin API/worker composition roots.
5. Create Alembic bases and migrations for organisations, users, sessions, API keys, customers, payments, accounts, journals, and postings.
6. Seed two organisations, administrators, scoped merchant keys, INR ledger accounts, and a bundled receiver endpoint version.
7. Implement session/API-key authentication, CSRF for session mutations, organisation-scoped repository conventions, and safe public IDs.
8. Implement ledger posting service, capture/refund journal templates, deferred balance validation, and immutability triggers.
9. Add real-PostgreSQL tests for balanced journals, minimum postings, wrong currency, missing references, update/delete rejection, and tenant composite keys.

### Done Criteria

- A fresh environment starts and migrates with one documented command.
- Both seeded administrators can authenticate only into their organisation.
- Provider/RelayPay app roles cannot read the other database.
- An unbalanced, underspecified, changed, or deleted posted journal fails at the database boundary.
- Ledger balances are derived from postings and all Week 1 tests pass on PostgreSQL.

## Week 2 — Payments, Idempotency, Reservations, and Provider Effect

### Goal

Implement safe command initiation and guarantee one provider operation/effect for each logical command.

### Tasks

1. Add payment, authorization, capture, refund, provider-operation, provider-attempt, and idempotency migrations/constraints.
2. Implement strict Pydantic DTOs, integer-paise/fixed-INR validation, canonical JSON fingerprinting, and key digest storage.
3. Implement customer creation and unique merchant-customer conflict behavior.
4. Implement the payment-creation transaction and explicit race recovery for:
   - same idempotency key;
   - different keys with the same merchant reference.
5. Implement payment-lock-first initiation transactions and the global lock order.
6. Implement singleton authorization/capture attachment in every operation state.
7. Implement refund reservations using child rows under the payment lock and derived availability.
8. Build the mock-provider database/API with stable-key uniqueness, signed responses, lookup endpoint, and deterministic one-shot faults.
9. Implement the `SENT`-before-HTTP persistence boundary and prohibit mutation resend after recorded send.
10. Add integration/concurrency tests for key reuse, terminal replay, singleton races, refund over-reservation, raw uniqueness error suppression, and stable provider effects.

### Done Criteria

- All four financial POST routes enforce the documented idempotency policy.
- Same-key creation races replay/conflict by fingerprint; different-key/same-reference races return `MERCHANT_REFERENCE_CONFLICT` and commit no losing key.
- Concurrent authorization/capture produces one child and provider operation; compatible keys attach.
- Concurrent refunds never reserve more than captured value.
- A committed `SENT` record exists before provider HTTP, and a second mutation attempt is impossible.

## Week 3 — Outcome Matrix, Recovery, and Shared Finalization

### Goal

Resolve provider ambiguity through verified lookup and apply every terminal result exactly once locally.

### Tasks

1. Implement provider signature/schema/account/key/kind/amount/currency/reference validation.
2. Encode every outcome-matrix row as named classification logic and tests.
3. Add recovery scheduling, `SKIP LOCKED` claims, lease tokens/expiry, bounded backoff, and expired-lease reclamation.
4. Make every post-send retry status-only; test that mutation HTTP is never called again.
5. Implement `REQUIRES_REVIEW` reasons and administrative `retry_lookup` without manual outcome assertion.
6. Implement one shared idempotent finalizer for inline/recovery paths.
7. Atomically transition resource/operation, create the financial journal when applicable, create immutable event bytes, and update all attached idempotency records.
8. Release reservations only for verified failed refunds; retain review reservations.
9. Record local apply failures separately and move to `APPLY_FAILURE` review after bounded retries.
10. Add crash/race tests at send, lookup, finalizer, response persistence, and worker acknowledgement boundaries.

### Done Criteria

- Every outcome-matrix row has a named passing test.
- Timeouts, resets, 5xx, and non-business 4xx never become financial declines.
- Malformed, unsigned, contradictory, or mismatched evidence cannot finalize.
- Inline/recovery races produce one transition, journal/event where required, and one byte-stable result across all keys.
- Authorization success produces one event and zero journals.
- Expired recovery leases are reclaimed and no network call holds a database transaction open.

## Week 4 — Transactional Events, Delivery, and Backend Release Candidate

### Goal

Complete the committed-event-to-deduplicated-webhook path and pass the full backend go/no-go gate.

### Tasks

1. Add endpoint/version, event, recipient, delivery, and delivery-attempt migrations with immutability constraints.
2. Snapshot subscribed immutable endpoint-version IDs during finalization.
3. Implement transactional materialization with short `FOR UPDATE SKIP LOCKED` batches and no persistent materializing state.
4. Implement delivery leases, HMAC signing over timestamp + stored bytes, stable event body/ID, bounded retry, dead letter, and lease reclamation.
5. Build the bundled receiver with signature validation and event-ID/digest deduplication.
6. Add PostgreSQL polling loops so recovery/materialization/delivery progresses after Redis loss and restoration.
7. Complete organisation-scoped evidence read model with bounded eager loads and redaction.
8. Add structured logs, correlations, `/health/live`, and `/health/ready`.
9. Build a CLI scenario for the lost capture response and print machine-checkable invariant counts/digests.
10. Run the full gate repeatedly from clean migrations and with injected worker crashes.

### Week-Four Go/No-Go Gate

All must pass against real PostgreSQL and Redis before console work begins:

- complete provider outcome matrix;
- idempotency and singleton races;
- both payment-creation race branches;
- refund-reservation concurrency;
- shared-finalizer crash/race tests;
- ledger invariants and immutability;
- event-byte and endpoint-version immutability;
- outbox materialization crash recovery;
- delivery lease expiry/reclamation and receiver deduplication;
- Redis-outage progress;
- tenant isolation, session, scope, CSRF, redaction, and rate limiting;
- CLI lost-response demonstration.

### Done Criteria

- The CLI proves one provider effect, capture, balanced journal, event, delivery, and stable replay after a lost response.
- Event bytes/digest and captured recipients do not change after finalization.
- A materializer or delivery worker crash leaves work selectable and safe to repeat.
- The receiver acknowledges duplicate delivery without repeating its consumer effect.
- The gate result is recorded in the repository with exact commands.

### If the Gate Is Red

Do not start the console. Continue backend correctness in Week 5, invoke the presentation cut list immediately, and protect the CLI/README demonstration as the release surface.

## Week 5 — Conditional Forensic Console

### Goal

After a green gate, make the correctness proof legible through the committed three-page console.

### Tasks

1. Implement `/login` with seeded demo identity selection, CSRF, rate-limit messaging, and safe redirect behavior.
2. Implement `/lab` with the lost-response scenario first, durable correlation handle if needed, bounded polling, and proof assertions.
3. Implement `/payments/[paymentIntentId]` using the single evidence read model.
4. Render proof summary, lifecycle, idempotency, provider attempts/review, ledger, events, delivery, and sanitized JSON.
5. Add retry-lookup confirmation/action; webhook replay only if schedule remains healthy.
6. Apply responsive behavior, semantic structure, keyboard support, focus management, contrast, and reduced-motion behavior from the design brief.
7. Add exactly one Playwright smoke test for the primary lost-response journey.
8. Preserve backend test time; do not replace correctness tests with UI tests.

### Done Criteria

- The three committed pages work for each seeded organisation and never expose cross-tenant data.
- A reviewer can run the primary scenario and trace every proof assertion to evidence.
- Keyboard-only, 320px, 200% zoom, contrast, and focus checks pass.
- The Playwright smoke test passes against the Compose stack.
- If the gate was not green, Week 5 instead ends with a stable CLI demo and a documented console cut.

## Week 6 — Release, Deployment, and Portfolio Evidence

### Goal

Ship a reproducible, secure-by-scope public project and optional hosted sandbox without weakening correctness.

### Tasks

1. Add coordinated sandbox reset that safely restores only synthetic seeded state.
2. Add CI for lint, type checks, migrations from empty DB, PostgreSQL/Redis integration suites, and the one browser smoke test if present.
3. Deploy Compose to Ubuntu LTS with Caddy-managed HTTPS, least-privilege secrets, backups appropriate for a sandbox, and resource limits.
4. Verify readiness, session cookies, CSRF, redaction, rate limits, database role isolation, and public receiver allowlist in production configuration.
5. Write README quickstart/demo, ADRs for idempotency/recovery/outbox/ledger, architecture diagram, threat model, limitations, and test evidence.
6. Run migration, reset, crash-recovery, Redis-outage, and lost-response smoke tests on the release candidate.
7. Publish the repository; publish a hosted sandbox only if operational safety and reset are ready.
8. Record a concise demo video only after the runnable proof is complete.

### Done Criteria

- A fresh clone reproduces the system and primary proof from documentation.
- CI is green from empty databases.
- HTTPS deployment exposes only intended routes and uses secure cookies/secrets.
- The release documentation makes synthetic-data and non-production limitations unmistakable.
- Final ship gate passes in the chosen release surface (console or CLI).

## Mandatory Test Matrix

### Idempotency

- Cross-payment, cross-command, and cross-version key reuse returns `409`.
- No committed nonterminal key lacks an operation target.
- Every attached key receives identical terminal bytes/status.
- Terminal attachment copies bytes within the attachment transaction.
- Both payment-creation race branches return their named behavior and leak no raw DB error.

### Provider and Concurrency

- Concurrent singleton commands create one provider operation/effect.
- Concurrent refunds cannot over-reserve.
- `SENT` commits before HTTP; mutation is never resent after send.
- Every outcome-matrix observation maps to its specified state.
- Request/recovery/finalizer races converge on one result.
- Persistent apply failure enters review without falsifying provider outcome.

### Ledger and Delivery

- Successful capture/refund creates one balanced immutable journal.
- Authorization creates no journal.
- Missing, unbalanced, changed, or deleted financial history fails.
- Event bytes and endpoint recipients never change.
- Materializer crash leaves work selectable.
- Expired delivery lease is reclaimed.
- Redis outage cannot permanently strand committed work.
- Receiver deduplicates redelivery after worker crash.

### Security and Tenancy

- Every cross-tenant user path returns `404`.
- Composite keys reject cross-tenant relationships.
- Merchant API keys receive `403` for evidence/admin endpoints.
- Sessions, scopes, CSRF, secret storage, and log/evidence redaction pass.
- Rate-limited routes return `429` with `Retry-After`.

## Never Cut

- Route-aware idempotency and exact terminal replay
- Correct payment-creation transaction and both race branches
- Singleton provider operations and compatible attachment
- Refund reservation under payment lock
- `SENT`-before-HTTP and status-only recovery
- Complete outcome matrix and shared finalizer
- Ledger balance/immutability constraints
- Immutable recipient snapshots and event bytes
- Transactional materialization and reclaimable delivery leases
- Tenant isolation and crash/concurrency tests

## Presentation Cut Order

1. Visual polish beyond accessible functional UI
2. Browser automation beyond one smoke test
3. Console webhook replay controls
4. Hosted sandbox; retain Compose + CLI proof
5. Scenario Lab; retain Payment Evidence deep link plus CLI
6. Entire console; retain CLI, README, test evidence, and demo recording

## Final Definition of Done

RelayPay is finished for this scope when:

- all mandatory tests pass against real dependencies;
- the release surface proves the lost-response sequence end to end;
- counts and digests substantiate one provider effect, one local transition, balanced journal, immutable event, at-least-once delivery, and stable replay;
- cross-tenant and secret-redaction tests are green;
- clean setup, migration, seed/reset, test, demo, and deployment procedures are documented and reproducible;
- all deferred and out-of-scope work remains explicitly labeled rather than partially implemented.
