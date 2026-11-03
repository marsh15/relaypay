# Limitations and non-goals

## Scope

- Synthetic INR data only; no card, bank, UPI, KYC, PII, real settlement, real fee schedule, dispute,
  or chargeback processing. The schema and finalizer carry synthetic per-merchant fee schedules and
  the platform fee and GST fields (D7), but no fee computation is implemented in this checkout: the
  calculator, `packages/relaypay/fees/calculator.py::compute_capture_fee`, is a hand-written stub
  that raises `NotImplementedError`, and capture reaches it through the fee service. The
  calculation is the maintainer's to write.
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
- One capture per payment, of up to the authorized amount; the remainder is released (partial
  capture, M16). Bounded evidence sizes are deliberate demonstration constraints.
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
- Redpanda is a startup dependency of the poller, not an optional integration. Compose's
  `depends_on` starts the `poller` service only after Redpanda's healthcheck passes. At run time the
  outbox publish step is the last step of each poller tick. It builds a Kafka producer, and with the
  broker unreachable that construction fails, so the publish step is skipped and the outbox rows wait.
  Financial tasks such as `recover_provider_operations` run earlier in the same tick and do not wait
  for the broker. Ordinary batch errors are isolated and logged per task; a failure in the final
  outbox publish step does not postpone other tasks' deadlines. A simulated publisher outage is
  covered by scheduler unit tests; a live broker-stop recovery drill is not recorded here.
  See the [runbook](operations/runbook.md#redpanda-and-the-outbox).
- Redpanda runs in `dev-container` mode with no data volume, in the production overlay as well.
- Merchant webhooks support three event types (`payment.authorized.v1`, `payment.captured.v1`,
  `refund.succeeded.v1`) to one bundled receiver.
- Inbound PSP webhooks are accepted only from the provider simulator (`SIMULATOR`). The Razorpay and
  Cashfree adapters are parsers and verifiers. They are not routed into the inbox, and they have no
  per-provider secrets in configuration.
- PSP webhook ordering is not implemented. Every matched PSP event ends `DEFERRED` and changes no
  operation; operations resolve by polling. The applier does not re-lease `DEFERRED` events.
- Provider webhook chaos (drop, duplicate, delay) is not in the chaos harness, and the harness does
  not replay webhooks.

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
- Request body caps are per service: 2 MiB for the API, receiver, and console proxy, 20 MiB for the
  dispute network, and 1 MiB at the edge. Each bounds the bytes one request can make its service
  read. They are not a rate limit, a concurrency limit, or an availability guarantee. The provider,
  bank, commerce, recovery-network, and merchant-site services have no cap in this checkout.
- A native COPY-only dummy-context build verified representative nested `.dockerignore`
  exclusions and template retention. It did not inspect real credentials or establish the contents
  of a deployed application image.
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

## Verification

- The 15 money invariants are not evaluated. The checker, `packages/relaypay/proof/invariants.py`,
  is hand-written by the maintainer and is still a stub. The chaos harness reports counters and
  harness failures only, and it prints `INVARIANTS NOT EVALUATED`. A passing chaos run is not a
  finding of zero invariant violations. See [resilience proofs](operations/resilience-proofs.md).
- The PSP ordering decision (`packages/relaypay/psp/ordering.py::decide`) is also hand-written and
  pending.
- The fee calculator (`packages/relaypay/fees/calculator.py`) and the payout locking strategies are
  hand-written and pending as well. See [PROVENANCE.md](../PROVENANCE.md).
- Until these four maintainer-written modules are implemented and their `handwritten` tests pass,
  the project is not complete.
- The 2026-11-03 source audit ([report](security/audits/2026-11-03/REPORT.md)) reviewed commit
  `41f3774` plus a pre-existing working-tree edit. It retained 0 confirmed vulnerabilities, 24
  needs-validation records, and 1 rejected claim across 151 coverage units (119 covered, 31 linked
  to candidate records, 1 blocked). The needs-validation records are unresolved conditional risks,
  not confirmed findings, and carry no severity until their actor, deployment, runtime, or impact
  prerequisites are established. The blocked unit concerns effective Caddy and Uvicorn
  forwarded-header behavior and needs an owner-observed topology; no ingress fixture was run. The
  audit is not a guarantee of security or proof that a deployment is safe, and it does not cover
  changes made after its snapshot.

## Documentation and tooling inconsistencies

These are known and not changed in code; the documentation works around them.

- The API reports version `2.0.0` (`contracts/openapi/v2.0.0.json`) while v2.0 is being built
  milestone by milestone; the simulators and the edge Worker still report `1.0.1`, the last
  released project version.
- `PUBLIC_BASE_URL` defaults to port 8080, but nothing listens there; the API is on 8000. The
  setting is only validated in production.
- The example value of `WEBHOOK_SECRET_ENCRYPTION_KEY` still contains the word "fernet" (changing
  it would make existing development databases unable to decrypt their endpoint secrets); the
  key is hashed for AES-GCM, as the comment in `.env.example` says. `LOG_LEVEL` is declared and
  unused, and the `integration` pytest marker text still mentions Redis.
- `docs/vibe-coding/` and `docs/phase-2/` are frozen planning documents that still describe Celery
  and Redis and earlier milestone numbering; see [docs/README.md](README.md).
