# Limitations and non-goals

## Scope

- Synthetic INR data only; no card, bank, UPI, KYC, PII, real settlement, fee, dispute, or
  chargeback processing.
- Every counterparty is a local simulator: one deterministic mock provider, one mock bank, one mock
  commerce store, in-memory dispute and recovery networks, static merchant-site snapshots, and one
  exact allowlisted bundled receiver. The dispute network keeps submissions in memory, so a restart
  loses the lookup truth for earlier submissions.
- The "model" roles (settlement intent classification and narrative, risk findings, dispute
  drafting) are deterministic in-process code. No external LLM is called by the application, token
  counts are estimated as `len(prompt)/4`, and every provider is priced at zero in
  `model_invocations`. The provider fallback router and PII-tokenizing prompt handling are tested
  but have no vendor transport. `scripts/compare_live_providers.py` is the only code that calls
  external model APIs; it needs vendor keys, is inert without them, and never runs in CI.
- The leased workflow-step engine in `agent_runtime/workflows.py` is covered by tests but is not
  called by the API or poller as of 1.0.1. Production workflows run in their own domain services.
  `ToolInvocation` rows are never written.
- Single full capture per payment; no partial capture. Bounded evidence sizes are deliberate
  demonstration constraints.
- Reconciliation accepts bounded CSV/JSON statements from the synthetic payment provider only; there
  are no connector-specific statement formats. Mismatch resolution records operator notes and may
  link an existing compensating journal, but it never creates or changes payment, provider, or
  ledger outcomes. No code path posts a `COMPENSATION` journal.
- Merchant settlement and payout administration are API-first. The synthetic bank is a
  deterministic test boundary, not a real transfer rail.
- Connector and commerce integrations are deterministic synthetic boundaries. Commerce excludes
  inventory, fulfillment, taxes, distributed sagas, and real merchant credentials. Connector
  circuit state exists but is recorded only by tests; traffic is not gated on it, and adapters are
  used only for health verification.
- Disputes can be opened only by scripts or the unused intake function; there is no API route to
  create one. Ambiguous dispute submissions are recovered only when an operator re-submits the
  package, not by the poller. The recurring-payment failure path is created in-process by an admin
  route rather than read from the event stream.

## Events and messaging

- Business-event outbox rows are published to Redpanda and never consumed in this repository.
  Publication failures retry forever with backoff and have no dead-letter state. Topic names double
  the version suffix for event types that already end in `.v1`.
- Redpanda runs in `dev-container` mode with no data volume, in the production overlay as well.
- Merchant webhooks support three event types (`payment.authorized.v1`, `payment.captured.v1`,
  `refund.succeeded.v1`) to one bundled receiver.

## Security and exposure

- The compose stack seeds two demo organisations with fixed, public development passwords on every
  `up`, including with the production overlay. Remove or rotate them before exposing a host.
- In-process login rate limiting is per API process; a distributed limiter is required before
  horizontal public deployment. The limiter keys on the ingress-supplied `X-Forwarded-For` chain
  when present (so users behind the console proxy or Caddy do not share one budget); that header
  is only as trustworthy as the ingress that sets it, and a hostile direct-to-API client can
  spoof it to rotate keys. The sandbox assumes the API port is not directly exposed.
- Merchant API-key routes have no rate limit at the API. The optional edge Worker adds a
  per-key-prefix per-minute window when it fronts the API.
- The API's `/metrics` endpoint and the poller's metrics port are unauthenticated by design
  (CI and the local Prometheus scrape them directly). They expose route shapes, latencies, and
  counters (including connector ids on one gauge), but no secrets or personal data, so the sandbox
  keeps them network-internal; any real deployment must put them behind an authenticated scraper
  or network boundary.
- The simulators' business endpoints are unauthenticated and rely on network isolation. Compose
  passes every database URL, including migrator URLs, to every Python container; role separation
  is a database property only.
- Tenant isolation is enforced by query predicates and composite foreign keys; there is no
  row-level security.
- Immutability is enforced by database triggers for the tables created through migration 0011,
  the provider database, and (migration 0018) 24 of the 39 tables added in migrations 0012 to
  0017: 17 reject any `UPDATE` or `DELETE`, and 7 versioned or lifecycle-bearing tables freeze
  every column except their status or replay columns and reject `DELETE`. The other 15 are
  mutable workflow state (leased outbox and workflow rows, status machines such as disputes,
  subscriptions, recovery cases and risk reviews, the hourly usage rollups, and request logs
  that are pruned by retention), so only application code constrains them. The bank and
  commerce databases have no triggers either. A privileged connection can still update the
  unprotected tables. `TRUNCATE` bypasses every row trigger, which the reset relies on.
- The optional edge origin boundary (opt-in via `EDGE_ORIGIN_SIGNATURE_REQUIRED`) verifies the
  HMAC, timestamp window, and nonce format of each request, but the origin keeps no replay
  store: a captured signed request can be replayed within the timestamp window unless the origin is
  reachable only through the edge worker, which does track replay keys. Production deployments must
  network-restrict the origin to the worker. Compose does not forward the `EDGE_*` variables to
  containers, and the Worker has no dead-letter queue and never prunes its replay and rate state.

## Operations

- No multi-region operation, automatic failover, point-in-time recovery, managed KMS, WAF, or
  production observability backend. The bundled collector exports traces to its debug exporter
  only.
- The bundled Prometheus configuration does not scrape the dispute, recovery, or merchant-site
  simulators. See [observability](operations/observability.md).
- "Single scheduler" is a convention, not an enforced lock. Running several pollers is safe because
  of `SKIP LOCKED` and leases, but it is not a tested scale-out configuration. Work is polled at
  about one-second granularity.
- The Compose deployment is an optional single-host Ubuntu LTS sandbox, not a PCI-DSS or
  regulated production architecture. Caddy terminates HTTPS, but host firewalling, OS patching,
  DNS, monitoring, restore drills, and secret rotation remain operator responsibilities.
- The reset operation is coordinated but not atomic across the four databases and the receiver
  schema; application workers must be stopped, as documented.
- Repository publication, hosted deployment, and demo-video recording require owner-controlled
  external accounts and are intentionally not automated by this codebase.
- Portfolio analytics aggregates are organisation+environment scoped queries computed on demand;
  there is no warehouse, materialized view, incremental rollup, or long-term metric history beyond
  the current Prometheus gauge values.
- Handling-time baselines and analytics shapes are versioned constants in code, not operator
  configuration; changing them is a code change, not a setting. The settlement and risk "model"
  providers are likewise fixed in `build_admin_router`.
- The versioned evaluation fixtures pin current deterministic behaviour; regenerating them is a
  deliberate, reviewed change (via the checked-in generator), not an automated step.

## Documentation and tooling inconsistencies

These are known and not changed in code; the documentation works around them.

- The API reports version `1.0.0` because it is pinned to the frozen OpenAPI contract
  (`contracts/openapi/v0.9.0.json`, `info.version` `1.0.0`); changing it means regenerating the
  contract and SDK. The simulators and the edge Worker report `1.0.1`, the project version.
- `PUBLIC_BASE_URL` defaults to port 8080, but nothing listens there; the API is on 8000. The
  setting is only validated in production.
- The example value of `WEBHOOK_SECRET_ENCRYPTION_KEY` still contains the word "fernet" (changing
  it would make existing development databases unable to decrypt their endpoint secrets); the
  key is hashed for AES-GCM, as the comment in `.env.example` says. `LOG_LEVEL` is declared and
  unused, and the `integration` pytest marker text still mentions Redis.
- `docs/vibe-coding/` and `docs/phase-2/` are frozen planning documents that still describe Celery
  and Redis and earlier milestone numbering; see [docs/README.md](README.md).
