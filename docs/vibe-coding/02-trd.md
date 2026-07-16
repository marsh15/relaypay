# 02 — Technical Requirements Document

## System Shape

RelayPay is a modular monolith plus two small companion services:

```text
Next.js forensic console
        │ HTTPS/session
        ▼
FastAPI RelayPay API ── PostgreSQL relaypay database
        │ HTTP                    ▲
        ▼                         │ polling/leases
Mock Provider API ─── PostgreSQL provider database
                                  │
Celery workers ◀──── Redis broker ┘
        │ signed HTTP
        ▼
Deduplicating webhook receiver
```

PostgreSQL owns all payment, operation, retry, lease, outbox, and delivery truth. Redis is a broker and wake-up optimization only. No database transaction or row lock remains open during network I/O.

## Frozen Stack

### Backend

- Python 3.12+
- FastAPI with Pydantic v2 DTOs
- Synchronous SQLAlchemy 2 and Psycopg 3
- Alembic migrations
- Celery workers with Redis as broker only
- Pytest integration and concurrency tests against real PostgreSQL and Redis

### Frontend

- Next.js App Router
- TypeScript with strict mode
- Server-rendered session checks and bounded evidence fetches
- Accessible semantic components; CSS implementation may use Tailwind if pinned in the initial lockfile
- Playwright for one primary lost-response smoke test

### Infrastructure

- One PostgreSQL server with separate `relaypay` and `provider` databases
- Separate least-privilege database roles; neither application role can read the other database
- A separate receiver role owns only a `receiver` schema inside the `relaypay` database; RelayPay application roles cannot read or write receiver deduplication rows
- Docker Compose for local and VPS deployment
- Caddy for HTTPS and reverse proxying
- Ubuntu LTS target: 4 vCPU, 8 GB RAM, 80 GB SSD

## Deployable Processes

- `api`: RelayPay FastAPI HTTP server
- `worker`: provider recovery, event materialization, and webhook delivery tasks
- `scheduler`: PostgreSQL-backed polling and lease acquisition
- `provider`: mock-provider HTTP server with its own database credentials
- `receiver`: bundled webhook receiver that deduplicates by event ID
- `console`: Next.js application
- `postgres`, `redis`, `caddy`: infrastructure services

Workers may share a code image but must expose distinct task queues and concurrency settings for recovery, materialization, and delivery.

## Backend Modules and Interfaces

- `Identity`: organisations, principals, sessions, API keys, CSRF, tenant scope
- `Payments`: customers, payment intents, authorization, capture, refunds, legal-state checks
- `ProviderOperations`: stable keys, send protocol, response validation, lookup recovery, review
- `Ledger`: accounts, journals, postings, validation, immutable reads
- `EventDelivery`: immutable events, recipient snapshots, materialization, leases, signing, retry
- `DemoScenarios`: deterministic fault setup and evidence references

HTTP routes, CLI commands, schedulers, and Celery tasks are thin adapters around module services. Module services own transaction boundaries and return typed results; adapters translate those results to HTTP, CLI, or task acknowledgements.

## Money and Payment Rules

- Store money as positive signed 64-bit integer paise; never use floating point.
- Currency is a fixed `INR` enum/value and is included in provider validation.
- A payment has at most one authorization and one full capture.
- Capture requires verified authorization `SUCCEEDED`.
- Refund requires capture `SUCCEEDED`.
- Authorization and capture are single-shot after verified business decline.
- Refund availability is calculated under a locked payment row:

```text
captured amount
− SUCCEEDED refund amounts
− PROCESSING refund amounts
− REQUIRES_REVIEW refund amounts
```

- Derived payment status and refundable amount are display-only read-model values, never command-authorization inputs.

## Authentication and Authorization

- Console: opaque random session ID in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie; session data is server-side.
- Session mutations require a CSRF token bound to the session.
- Merchant API: bearer API key shown once, persisted as public prefix plus peppered digest.
- API permissions are explicit scopes; evidence and administrative recovery are never available to merchant keys.
- User-facing resource access always queries `(organisation_id, resource_id)`.
- Cross-tenant resources return `404`; authenticated-but-unauthorized same-tenant actions return `403`.
- Workers accept trusted internal database identifiers, not organisation IDs supplied by end users.

## HTTP Contract

### Session Routes

- `POST /api/session/login`
- `POST /api/session/logout`
- `GET /api/session/me`

### Session-Only Demo Routes

- `POST /api/demo/scenarios`: start one allowlisted synthetic scenario and return `202` with its correlation/run ID.
- `GET /api/demo/scenarios/{scenario_run_id}`: return bounded progress, proof assertions, and the resulting payment ID when available.

These unversioned routes are administrative console adapters around `DemoScenarios`; they are outside the merchant financial API and its idempotency fingerprint protocol. Scenario orchestration must call the same payment application services/API contracts as normal clients and may configure only allowlisted one-shot mock-provider faults.

### V1 Routes

- `POST /api/v1/customers`
- `POST /api/v1/payment_intents`
- `GET /api/v1/payment_intents/{payment_intent_id}`
- `GET /api/v1/payment_intents/{payment_intent_id}/evidence`
- `POST /api/v1/payment_intents/{payment_intent_id}/authorize`
- `POST /api/v1/payment_intents/{payment_intent_id}/capture`
- `POST /api/v1/payment_intents/{payment_intent_id}/refunds`
- `GET /api/v1/operations/{operation_id}`
- `POST /api/v1/operations/{operation_id}/retry_lookup`
- `GET /api/v1/webhook_deliveries/{delivery_id}`
- `POST /api/v1/webhook_deliveries/{delivery_id}/replay`

### Health Routes

- `GET /health/live`: process is running; no dependency calls
- `GET /health/ready`: RelayPay database and required schema are available; expose dependency-specific readiness without secrets

### Status Semantics

- `200`: successful read/action or terminal command replay when the canonical result is `200`
- `201`: created customer/payment intent; payment-creation replay preserves `201`
- `202`: known nonterminal provider operation
- `400`: missing idempotency header or malformed command-level input
- `401`: missing or invalid authentication
- `403`: authenticated principal lacks permission
- `404`: absent or cross-tenant resource
- `409`: idempotency, merchant reference, or legal-state conflict
- `422`: Pydantic request-schema failure
- `429`: documented rate limit exceeded, with `Retry-After`
- `503`: temporary database/dependency unavailability, with `Retry-After`

Terminal idempotent replays include `Idempotency-Replayed: true`.

## Structured Idempotency

Require `Idempotency-Key` for payment creation, authorization, capture, and refund. A missing key returns `400 IDEMPOTENCY_KEY_REQUIRED`.

The fingerprint is SHA-256 over UTF-8 canonical JSON built from validated Pydantic output:

```json
{
  "api_version": "v1",
  "method": "POST",
  "route_template": "/payment_intents/{payment_intent_id}/capture",
  "path_params": {"payment_intent_id": "pay_123"},
  "body": {}
}
```

Canonicalization rules:

- reject unknown fields before hashing;
- use decoded canonical identifiers;
- sort object keys and use stable separators;
- include method, API version, route template, path parameters, and validated body;
- store the canonical fingerprint input for safe debugging, without secrets;
- reject any same-organisation key with a different fingerprint as `409 IDEMPOTENCY_KEY_REUSED`.

### Payment Creation Transaction

1. Validate DTO/header and generate payment ID in application code.
2. Begin one transaction and read the organisation-scoped key.
3. Replay matching terminal state or reject a mismatched fingerprint.
4. Insert payment with unique `(organisation_id, merchant_reference)`.
5. Serialize the canonical payment response once.
6. Insert a terminal idempotency record containing target, status, headers needed for replay, and exact response bytes.
7. Commit.

On a same-key uniqueness race, roll back everything, re-read the winner in a new transaction, and replay/conflict by fingerprint. On a different-key/same-reference race, roll back everything, find the existing payment, return `409 MERCHANT_REFERENCE_CONFLICT`, and do not create an idempotency row for the losing key.

### Operation Initiation Transaction

1. Validate auth, header, and DTO without writes.
2. Begin a short transaction and lock the organisation-scoped payment `FOR UPDATE`.
3. Read the idempotency key; replay terminal, return current `202`, or reject mismatch.
4. Validate durable child state while holding the payment lock.
5. Attach compatible authorization/capture keys to the singleton operation, regardless of its state; otherwise create the child and operation.
6. For refund, validate availability and reserve by inserting the refund under the payment lock.
7. Insert the idempotency record with a non-null operation/resource target.
8. Commit before any provider HTTP.

If a uniqueness constraint selects another winner, roll back every provisional row and return the winner's state. A nonterminal idempotency record may never commit without its target.

## Lock Order

All transactions that need multiple locks use:

```text
payment_intent
→ provider_operation
→ authorization/capture/refund
→ idempotency_records ordered by id
```

Schedulers acquire and commit leases in short transactions. Network calls occur after commit.

## Provider Send and Recovery Protocol

Stable keys are:

```text
authorize:{payment_intent_id}
capture:{payment_intent_id}
refund:{refund_id}
```

Before the first mutation HTTP call, persist canonical provider request bytes, create a `SENT` attempt, increment the operation attempt count, set `last_sent_at`, and commit. Once a send is recorded, all later work is status lookup only.

Provider responses are accepted only after signature, schema, provider account, stable key, operation kind, amount, currency, and reference all match.

| Observation | RelayPay state/action |
|---|---|
| Valid matching success | finalize `SUCCEEDED` |
| Valid matching business decline | finalize `FAILED` |
| Timeout/connection reset | remain `PROCESSING`; lookup |
| HTTP 5xx | remain `PROCESSING`; lookup |
| Non-business HTTP 4xx | lookup; eventually review |
| Malformed/unsigned/contradictory/mismatched | `REQUIRES_REVIEW` |
| Lookup pending | remain `PROCESSING` |
| Lookup matching success | finalize `SUCCEEDED` |
| Lookup verified business failure | finalize `FAILED` |
| Lookup remains indeterminate | review with `PROVIDER_INDETERMINATE` |
| Repeated local apply error | review with `APPLY_FAILURE` |

Review permits evidence inspection and `retry_lookup` only. No API accepts a manually asserted provider result.

## Shared Finalizer

Every inline and recovery path calls the same idempotent finalizer. In one transaction it:

1. Locks in the required global order.
2. Returns the stored result if another caller already finalized.
3. Applies a verified conditional child/operation transition.
4. Creates exactly one capture/refund journal when required.
5. Creates one immutable merchant event.
6. Stores canonical terminal status, headers, and response bytes on the operation.
7. Copies the exact result to every attached idempotency row ordered by ID.
8. Releases reservation only for a verified failed refund.
9. Commits all effects together.

An invariant failure rolls back all effects. Local apply failures are recorded separately; bounded retries eventually move the operation to review without changing provider truth.

## Ledger Contract

Capture:

```text
Debit  Provider Clearing Asset
Credit Merchant Payable Liability
```

Refund:

```text
Debit  Merchant Payable Liability
Credit Provider Clearing Asset
```

The domain and database enforce equal debit/credit totals, at least two postings, unique journal per successful capture/refund operation, mandatory journal reference from successful financial children, and immutability after posting. Balances are computed from postings.

## Event and Delivery Contract

- Events: `payment.authorized.v1`, `payment.captured.v1`, `refund.succeeded.v1`.
- The finalizer stores exact serialized event bytes, schema version, and immutable endpoint-version recipients.
- A short `FOR UPDATE SKIP LOCKED` transaction materializes one delivery per event/recipient. There is no persistent `MATERIALIZING` state.
- Delivery workers acquire lease token/expiry, commit, perform signed HTTP, and record immutable attempts in a later transaction.
- The signature is HMAC-SHA256 over timestamp plus stored bytes; retries keep stable event ID and body.
- Retry is bounded with terminal `DELIVERED` or `DEAD_LETTER`; expired leases are reclaimable.
- Manual replay creates a new delivery execution record linked to the original event/recipient without changing event bytes.
- The receiver deduplicates by event ID and returns success for known redelivery.
- PostgreSQL polling guarantees progress without Redis notifications.

## Folder Structure

```text
relaypay/
├── apps/
│   ├── api/                  # FastAPI routes and composition root
│   ├── console/              # Next.js forensic console
│   ├── provider/             # mock provider HTTP app
│   └── receiver/             # deduplicating webhook receiver
├── packages/
│   └── relaypay/             # Python domain/application/infrastructure modules
│       ├── identity/
│       ├── payments/
│       ├── provider_operations/
│       ├── ledger/
│       ├── event_delivery/
│       └── demo_scenarios/
├── migrations/
│   ├── relaypay/
│   └── provider/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── concurrency/
│   └── e2e/
├── scripts/                  # seed, reset, smoke, and scenario CLI
├── docs/                     # brief, ADRs, threat model, diagrams
├── compose.yaml
├── Caddyfile
└── .env.example
```

Use `snake_case` for Python modules/database names, `PascalCase` for React components, and stable prefixed public IDs (`org_`, `pay_`, `op_`, `evt_`) backed by UUIDs or UUIDv7 values.

## Environment Variable Names

No secrets are committed. `.env.example` documents names and safe development placeholders.

- `APP_ENV`
- `LOG_LEVEL`
- `PUBLIC_BASE_URL`
- `RELAYPAY_DATABASE_URL`
- `RELAYPAY_MIGRATION_DATABASE_URL`
- `PROVIDER_DATABASE_URL`
- `PROVIDER_MIGRATION_DATABASE_URL`
- `RECEIVER_DATABASE_URL`
- `REDIS_URL`
- `CELERY_BROKER_URL`
- `SESSION_COOKIE_NAME`
- `SESSION_SECRET`
- `CSRF_SECRET`
- `API_KEY_PEPPER`
- `WEBHOOK_SECRET_ENCRYPTION_KEY`
- `PROVIDER_BASE_URL`
- `PROVIDER_ACCOUNT_ID`
- `PROVIDER_SIGNING_SECRET`
- `PROVIDER_CONTROL_SECRET`
- `RECEIVER_BASE_URL`
- `RECEIVER_WEBHOOK_SECRET`
- `NEXT_PUBLIC_API_BASE_URL`
- `INTERNAL_API_BASE_URL`
- `CADDY_DOMAIN`

## Observability

- Emit structured JSON logs from every process.
- Propagate/generate request, payment, operation, event, delivery, and scenario correlation IDs.
- Log state transitions, lease acquisition/reclamation, provider validation classifications, retries, and finalization outcomes.
- Redact API keys, cookies, CSRF tokens, provider signatures, webhook secrets, and synthetic identity payloads.
- Evidence reads expose sanitized summaries, never raw secrets.

## Technical Constraints

- Synchronous SQLAlchemy only; do not mix sync and async transaction models.
- PostgreSQL is required for `FOR UPDATE`, `SKIP LOCKED`, constraints, and integration tests; SQLite is unsupported.
- Redis/Celery loss must not lose committed work.
- Provider and RelayPay application roles cannot cross-read databases.
- Unknown request fields are rejected.
- Event and terminal response bytes are serialized once and persisted as bytes.
- No mutation POST is retried after `SENT` commits.
- All migrations are forward-safe and tested from an empty database.
- Dependency versions and container images are pinned in lockfiles/configuration.
