# RelayPay

RelayPay is an evidence-first, INR-only payment-orchestration sandbox. It demonstrates how a
payment service can survive a provider committing a financial effect while its response is lost:
the mutation is never repeated, recovery uses a signed status lookup, and local state finalizes
exactly once with an immutable balanced ledger entry, event, and webhook trail.

v1.0.0 is the integrated platform release: the same PostgreSQL-authoritative core now spans
payments, reconciliation, dispute responses, subscription recovery, settlement-intelligence
questions, merchant risk review, and cross-workflow portfolio analytics — with tenant dimensions
kept in PostgreSQL, a versioned deterministic evaluation suite wired into CI, and one runnable
synthetic portfolio journey (`make portfolio-demo`).

**Synthetic data only. RelayPay is not a payment processor and must never receive real card,
bank-account, identity, or customer data.**

## Primary proof

The forensic console runs one deterministic lost-capture-response journey and proves:

- one provider capture effect and one local capture;
- one status-only recovery lookup after the ambiguous response;
- one balanced, immutable capture journal;
- byte-stable terminal responses across both attached capture keys;
- one immutable capture event and one acknowledged webhook delivery.

## Quickstart

Requirements: Docker with Compose, Python 3.12, [uv](https://docs.astral.sh/uv/), and Node.js 24.

```bash
cp .env.example .env
uv sync --frozen
npm ci --prefix apps/console
docker compose up --build
```

Open `http://localhost:3000/login` and use either synthetic administrator:

```text
admin@northstar.test / RelayPay-Northstar-2026!
admin@juniper.test   / RelayPay-Juniper-2026!
```

Select **Run lost-response scenario**, then follow **Inspect payment evidence**. The CLI proof is
also available while the stack is running:

```bash
make demo
```

## Developer gate

```bash
make lint
make typecheck
make test
make evaluations
make console-check
make console-e2e
```

Integration tests use PostgreSQL and Redis at the development ports from `.env.example`. Start
them with `make infra-up`, then run `make migrate` and `make seed` when not using the complete
Compose stack.

Upgrading to v1.0.0 from any earlier release adds no database migration: the integrated-platform
analytics layer is query-only, and the frozen merchant API remains compatible. Run the versioned
evaluation suite after upgrading. See the [v1.0.0 migration guide](docs/migrations/v1.0.0.md).

With the complete stack running, export a synthetic provider statement, import it into TEST,
and process its reconciliation run with:

```bash
RELAYPAY_DEMO_PASSWORD='RelayPay-Northstar-2026!' make reconciliation-demo
```

Run the permanent lost-response proof and settle its capture into available merchant balance:

```bash
make merchant-balance-demo
```

Run the versioned connector, inbound webhook, and commerce synchronization proof:

```bash
make connector-demo
```

Run one end-to-end synthetic journey across the integrated platform — payment authorization and
capture, a settlement question, a recovery case, a dispute draft, a risk review, and the portfolio
analytics summary — and replay the versioned v1.0.0 evaluation suite:

```bash
make portfolio-demo
make evaluations
```

To restore only synthetic state, stop application processes and use the explicit destructive
confirmation:

```bash
docker compose stop api provider receiver worker poller console
SANDBOX_RESET_CONFIRM=RESET_SYNTHETIC_RELAYPAY make reset
docker compose up -d
```

The reset leaves schemas and Alembic history intact, truncates only RelayPay/provider/receiver
application data, and reseeds both demo organisations. Production additionally requires
`ALLOW_SANDBOX_RESET=true`.

## System map

```mermaid
flowchart LR
    Browser["Forensic console"] --> Console["Next.js console"]
    Console --> API["RelayPay API"]
    Merchant["Synthetic merchant client"] --> API
    API --> RelayDB[("RelayPay PostgreSQL")]
    API --> Provider["Deterministic provider"]
    API --> RecoveryNetwork["Synthetic recovery network"]
    Worker --> Bank["Deterministic synthetic bank"]
    Bank --> BankDB[("Bank PostgreSQL")]
    Worker --> Commerce["Synthetic commerce"]
    Commerce --> CommerceDB[("Commerce PostgreSQL")]
    Provider --> ProviderDB[("Provider PostgreSQL")]
    Poller["Recovery / delivery poller"] --> RelayDB
    Poller --> Reconcile["Leased reconciliation runs"]
    Reconcile --> RelayDB
    Poller --> Provider
    Poller --> Receiver["Bundled allowlisted receiver"]
    Worker["Celery worker + beat"] --> Redis[("Redis acceleration")]
    Worker --> RelayDB
    Worker --> RecoveryNetwork
    Worker --> Forecasts["Immutable pre-cutoff settlement forecasts"]
    Forecasts --> RelayDB
    API --> MerchantSite["Synthetic merchant-site snapshots"]
    API --> RiskDB[("Risk reviews in RelayDB")]
    Receiver --> ReceiverSchema[("Isolated receiver schema")]
```

Correctness does not depend on Redis: PostgreSQL is the authority for operation state, leases,
ledger history, immutable event bytes, and delivery progress.

## Documentation

- [Architecture](docs/architecture.md)
- [Threat model](docs/threat-model.md)
- [Deployment and backups](docs/deployment.md)
- [Known limitations](docs/limitations.md)
- [Release test evidence](docs/test-evidence.md)
- [Architecture decisions](docs/adr/README.md)
- [PRD, TRD, flows, UI brief, schema, and frozen six-week plan](docs/vibe-coding/README.md)
- [Phase 2 product contract](docs/phase-2/product-contract.md)
- [Phase 2 implementation roadmap](docs/phase-2/implementation-roadmap.md)
- [v1.0.0 release notes](docs/releases/v1.0.0.md)
- [All release notes](docs/releases/)
- [Migration guides](docs/migrations/)
- [Operations telemetry](docs/operations/observability.md)
- [Resilience proofs](docs/operations/resilience-proofs.md)
- [Python SDK](docs/api/python-sdk.md)
- [API error catalog](docs/api/error-catalog.md)

## Technology

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Psycopg 3, Alembic, PostgreSQL 17,
Redis/Celery, Next.js 16 App Router, React 19, TypeScript, Playwright, Caddy, and Docker Compose.

RelayPay is available under the [MIT License](LICENSE). Release publication does not deploy or
host the sandbox.
