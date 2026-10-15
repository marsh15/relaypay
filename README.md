# RelayPay

RelayPay is a synthetic, INR-only payment-orchestration sandbox. Its core is a payment API that
stays correct when a provider commits a financial effect and the response never arrives: the
mutation is sent once, recovery uses a status lookup instead of a retry, and local state is
finalized exactly once with a balanced, immutable ledger entry, a merchant event, and a webhook
delivery.

Around that core the repository contains a merchant balance and payout ledger, statement
reconciliation, versioned connectors, dispute responses, subscription recovery, settlement
questions, merchant risk review, an operator console, and the supporting simulators (provider,
bank, commerce, dispute network, and others) needed to run all of it locally.

**Synthetic data only.** RelayPay is not a payment processor. Do not send it real card, bank,
identity, or customer data. All counterparties are local simulators, and the "model" roles in the
settlement, risk, and dispute workflows are deterministic code, not external LLM calls (see
[limitations](docs/limitations.md)).

Current version: **1.0.1** ([release notes](docs/releases/v1.0.1.md)). Licensed under the
[MIT License](LICENSE). Nothing in this repository deploys or hosts the sandbox for you.

## The problem it demonstrates

A capture request reaches the provider, the provider commits the capture, and the response is
lost. A naive client retries and captures twice; a cautious one fails the payment while the money
moved. RelayPay handles it with these rules:

1. The provider operation and a `SENT` attempt are committed to PostgreSQL **before** any HTTP
   call. A logical command has one stable provider key.
2. After a lost or ambiguous response the mutation is never resent. A worker issues a status-only
   lookup for the same stable key; the provider's signed reply is validated (signature, schema,
   account, kind, amount, currency, reference) before it is trusted.
3. One shared finalizer, in one transaction, writes the resource state, the ledger journal, the
   immutable merchant event, and the terminal response bytes for **every** idempotency key attached
   to the operation. Two capture keys therefore replay byte-identical responses.
4. A leased delivery worker sends the event as HMAC-signed bytes to an allowlisted receiver, which
   deduplicates by event id and digest.

The console's Scenario Lab and `make demo` run this journey deterministically and assert: one
provider effect, one capture, one balanced journal, one event, one acknowledged delivery, and
identical response bytes for both capture keys. Details: [architecture](docs/architecture.md),
[ADR-002](docs/adr/002-status-only-recovery.md).

## What is implemented

| Area | Capability |
| --- | --- |
| Payments | Customers, payment intents, authorize, capture (single, full), refunds against available amount; route-aware idempotency; cursor pagination |
| Ledger | Double-entry INR journals, DB-enforced balance and immutability, derived merchant balances (pending, available, receivable) |
| Events | Immutable merchant events, endpoint versions, leased HMAC-signed delivery, bounded retry, dead letter, operator replay |
| Settlement and payouts | Idempotent settlement runs, reserved payouts, numbered bank attempts with lookup-only recovery |
| Reconciliation | Immutable statement imports (CSV/JSON), leased versioned runs, matches, mismatch workflow |
| Connectors | Versioned connector config with encrypted credentials, health verification, authenticated inbound webhooks, synthetic commerce sync |
| Identity | Organisations with TEST and LIVE_LIKE environments, memberships and roles, scoped and rotatable API keys, session + CSRF admin access |
| Disputes | Draft versions with citations, digest-bound frozen packages, maker-checker approval, send-before-network submission |
| Subscription recovery | Bounded recovery cases, consent-aware actions, opt-out suppression |
| Settlement questions and risk review | Fixed tenant-scoped queries with citations; deterministic risk scoring over synthetic site snapshots, escalation, disposition |
| Analytics and evaluations | Portfolio aggregates computed in PostgreSQL; a versioned deterministic evaluation suite run in CI |
| Console | Next.js operator UI: Scenario Lab, payment evidence, operations, settlement, risk, recovery, disputes, agent runs |
| Edge (optional) | Cloudflare Worker source with local/CI proof only; not deployed by this repository |

## Architecture

![RelayPay system architecture](docs/diagrams/architecture.svg)

Editable source: [architecture.excalidraw](docs/diagrams/architecture.excalidraw) (open it at
[excalidraw.com](https://excalidraw.com)).

| Component | Role |
| --- | --- |
| `apps/api` | FastAPI service. Merchant API (`/api/v1`), admin API (`/api/admin/v1`, `/api/session`), signed inbound webhooks (`/api/inbound/v1`) |
| `apps/worker/poller.py` | The only background process. Time-gated batches for recovery, delivery, reconciliation, payouts, subscription recovery, inbound webhooks, forecasts, and the outbox drain |
| `apps/console` | Next.js 16 operator console that talks to the API through server components and a `/backend/*` proxy |
| `apps/provider`, `bank`, `commerce`, `dispute_network`, `communication_network`, `merchant_site` | Deterministic counterparty simulators, each its own container |
| `apps/receiver` | Reference webhook consumer with deduplication in an isolated schema |
| `apps/edge` | Optional Cloudflare Worker (rate and replay admission, R2 archive, Queue, signed origin requests) |
| `packages/relaypay` | Domain code, one package per area (`payments`, `ledger`, `provider_operations`, `event_delivery`, and so on) |
| `packages/relaypay_sdk` | Python SDK: hand-written facade over a client generated from `contracts/openapi` |
| `migrations/` | One Alembic tree per database: `relaypay`, `provider`, `bank`, `commerce`, `receiver` |

PostgreSQL is the only correctness authority. There is no task queue: the poller claims work with
`FOR UPDATE SKIP LOCKED` and expiring lease tokens, so a crashed poller restarts and reclaims its
work. Redpanda receives business-event outbox rows for downstream consumers, but nothing in this
repository consumes them and no financial outcome depends on it.

More detail: [architecture](docs/architecture.md), [database](docs/database.md),
[HTTP API](docs/api/reference.md), [threat model](docs/threat-model.md).

## Technology

| Component | Choice | Why |
| --- | --- | --- |
| API | Python 3.12, FastAPI, Pydantic v2 (strict models) | Typed request and response contracts; OpenAPI is generated and checked for drift in CI |
| Persistence | PostgreSQL 17, SQLAlchemy 2, psycopg 3, Alembic | `SKIP LOCKED` leases, advisory locks, deferred constraint triggers for ledger balance, partial unique indexes |
| Event stream | Redpanda (Kafka API) | Target for the transactional outbox; derived and at-least-once |
| Console | Next.js 16 (App Router), React 19, TypeScript | Server-side session handling; Playwright and axe for end-to-end and accessibility checks |
| Edge (optional) | Cloudflare Workers, Durable Objects, R2, Queues | Admission control and immutable ingress archive, proven locally with Wrangler dry runs |
| Ingress | Caddy | Automatic TLS and HTTP/3 for the single-host deployment |
| Observability (optional) | OpenTelemetry, Prometheus, Grafana | Bounded-cardinality metrics and traces behind a compose profile |
| Tooling | uv, ruff, mypy (strict), pytest, Docker Compose | Reproducible installs and a single release gate |

## Quick start

Requirements: Docker with Compose v2, Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 24.
Docker alone is enough to run the stack; Python and Node are needed for the `make` targets and the
test suites.

```bash
cp .env.example .env
docker compose up --build
```

Compose starts PostgreSQL and Redpanda, runs every Alembic tree, seeds two demo organisations,
then starts the API, simulators, receiver, poller, and console. Open
<http://localhost:3000/login> and sign in with a seeded administrator (these are public
development credentials from `scripts/seed.py`; never reuse them):

```text
admin@northstar.test / RelayPay-Northstar-2026!
admin@juniper.test   / RelayPay-Juniper-2026!
```

Choose **Run lost-response scenario**, then **Inspect payment evidence**.

The seed step prints each organisation's TEST API key once, in the `migrate` service log:

```bash
docker compose logs migrate | grep "API key"
```

Call the merchant API on port 8000 with that key:

```bash
KEY=rpk_test_...   # from the log above
curl -s localhost:8000/api/v1/customers \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"merchant_customer_reference":"cust-001","display_name":"Asha"}'

curl -s localhost:8000/api/v1/payment_intents \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: pi-001' \
  -d '{"customer_id":"cus_<id from above>","merchant_reference":"order-001","amount":50000}'
```

`amount` is an integer in paise. Authorize, capture, and refund are
`POST /api/v1/payment_intents/{id}/authorize|capture|refunds` with their own `Idempotency-Key`; they
return `202` and the operation settles asynchronously. See the [HTTP API reference](docs/api/reference.md).

## Local development

Run PostgreSQL and Redpanda from Compose and the Python tooling from the host:

```bash
uv sync --frozen
npm ci --prefix apps/console
npm ci --prefix apps/edge          # only for the edge Worker checks
docker compose up -d postgres redpanda
make migrate && make seed
make test
```

PostgreSQL is published on `localhost:55432` and Redpanda on `localhost:19092`; `.env.example`
already points host-run code at them. Integration tests need PostgreSQL, and the agent-runtime
outbox test also needs Redpanda. `make migrate` does not read `.env`: Alembic uses the
development URLs hard-coded in each `alembic.ini` unless you export the matching
`*_MIGRATION_DATABASE_URL` variables. `make seed` and the demos do read `.env`.

### Commands

| Command | Purpose |
| --- | --- |
| `make lint`, `make format-check`, `make typecheck` | ruff, ruff format, strict mypy over `packages apps scripts` |
| `make unit`, `make test` | pytest: unit only, or the full suite |
| `make api-contract`, `make sdk-drift`, `make sdk-example` | OpenAPI regeneration and compatibility check; SDK regeneration check; offline webhook-verification example |
| `make evaluations` | Deterministic evaluation suite (dispute, recovery, settlement, risk, adversarial fixtures) |
| `make demo` | Lost-response proof, run in-process against the seeded database |
| `make merchant-balance-demo`, `make connector-demo`, `make portfolio-demo` | Settlement, connector/inbound/commerce, and cross-workflow journeys against the database |
| `RELAYPAY_DEMO_PASSWORD=... make reconciliation-demo` | Export a provider statement, import it, run reconciliation (needs the full stack) |
| `make console-check`, `make console-e2e` | Console lint, typecheck, build, `npm audit`; Playwright journeys (needs the full stack) |
| `make edge-check`, `make compose-check` | Worker lint, types, tests, dry-run build; `docker compose config` validation |
| `make m7-evidence` | Query plans and bounded HTTP performance measurements |
| `make check` | Lint, format, types, contract and SDK checks, pytest, console and edge checks, compose validation (needs PostgreSQL and Redpanda up; not the Playwright journeys) |
| `make infra-up`, `make infra-down` | Start PostgreSQL only; `docker compose down` (keeps the data volume) |

### Resetting synthetic state

The reset truncates application data in all four databases and the receiver schema, keeps schemas
and Alembic history, and reseeds both organisations. Stop everything that writes first:

```bash
docker compose stop api poller console provider receiver bank commerce \
  dispute-network recovery-network merchant-site
SANDBOX_RESET_CONFIRM=RESET_SYNTHETIC_RELAYPAY make reset
docker compose up -d
```

In `APP_ENV=production` it additionally requires `ALLOW_SANDBOX_RESET=true`.

## Testing

CI (`.github/workflows/ci.yml`, one job) starts empty PostgreSQL and Redpanda, replays each
milestone's upgrade proof, applies all migrations, runs `alembic check` on all five trees, then
runs lint, format, strict typing, the OpenAPI and SDK drift checks, the pytest suite (unit,
integration, concurrency), the evaluation suite, the console build, the edge checks, compose
validation, the Playwright journeys with axe, and measured performance evidence. Test files live
in `tests/unit` and `tests/integration`; console journeys in `apps/console/e2e`; Worker tests in
`apps/edge/test`. Recorded release runs are in [docs/test-evidence.md](docs/test-evidence.md) and
[docs/evidence/](docs/evidence/).

## Configuration and deployment

All settings are environment variables validated at startup by `packages/relaypay/config.py`; the
reference is [docs/configuration.md](docs/configuration.md). Production mode rejects insecure
defaults for eight secrets and requires an HTTPS `PUBLIC_BASE_URL`.

[docs/deployment.md](docs/deployment.md) covers the optional single-host Ubuntu deployment
(`compose.production.yaml` plus Caddy), backups across all four databases, and reset. It is a
disposable-sandbox layout, not a regulated production architecture.

## Design decisions and trade-offs

- **PostgreSQL over a queue.** Recovery, delivery, reconciliation, and payouts are rows with
  leases, so every claim is observable and recoverable with SQL. The cost is polling latency
  (about one second per tick) and throughput bounded by one scheduler. Running a second poller is
  safe, but it is not a tested scale-out path.
- **Send-before-HTTP, lookup-only after.** A mutation can reach a provider at most once per stable
  key. After five lookups that stay indeterminate the operation becomes `REQUIRES_REVIEW`; a human
  decides, and the system never guesses.
- **Immutability by trigger.** Ledger, events, statements, and other evidence tables reject
  `UPDATE` and `DELETE` through database triggers. Tables added in migrations 0012 to 0017
  (agent runtime, disputes, recovery, settlement intelligence, risk review) are append-only by
  application convention only. See [database](docs/database.md#immutability).
- **No row-level security.** Tenant isolation comes from organisation and environment predicates
  in queries plus composite foreign keys. Foreign IDs return `404`.
- **Separate databases for each counterparty.** The provider, bank, and commerce simulators own
  their data so RelayPay code cannot read it. Compose still passes every database URL to every
  container; the separation is enforced by database roles, not container wiring.

## Known limitations

The full list is in [docs/limitations.md](docs/limitations.md). The ones most likely to matter:

- Synthetic INR only; one full capture per payment; the model roles are deterministic stand-ins.
- The compose stack seeds fixed, public demo credentials on every `up`, including with the
  production overlay.
- The login rate limiter is per process; API-key routes have no rate limit unless the optional
  edge Worker is placed in front.
- `/metrics` and the poller metrics port are unauthenticated and must stay network-internal.
- Redpanda runs in `dev-container` mode without a data volume, and no consumer reads it.

## Repository layout

```text
apps/            api, worker (poller), console, edge, simulators, receiver
packages/        relaypay (domain code), relaypay_sdk
migrations/      Alembic trees: relaypay, provider, bank, commerce, receiver
contracts/       Frozen OpenAPI documents and API fixtures
infra/           Caddy, Postgres bootstrap, OpenTelemetry, Prometheus, Grafana
scripts/         Seed, reset, demos, evaluation runner, contract and evidence checks
examples/python/ SDK and webhook examples
tests/           unit, integration, fixtures
docs/            Documentation (index: docs/README.md)
```

## Documentation

Start with the [documentation index](docs/README.md). Direct links:
[architecture](docs/architecture.md) · [database](docs/database.md) ·
[HTTP API](docs/api/reference.md) · [error catalog](docs/api/error-catalog.md) ·
[Python SDK](docs/api/python-sdk.md) · [configuration](docs/configuration.md) ·
[deployment](docs/deployment.md) · [threat model](docs/threat-model.md) ·
[limitations](docs/limitations.md) · [ADRs](docs/adr/README.md) ·
[domain glossary](CONTEXT.md) · [contributing](CONTRIBUTING.md)
