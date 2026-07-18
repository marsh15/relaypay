# RelayPay Phase 2 gate-driven roadmap

Each milestone is a thin vertical release. Work proceeds schema/contract → migration → domain
service → API/worker → tests → documentation. A red gate stops the release: no merge or tag may
claim a failed or unmeasured result.

| Milestone | Release | Required outcome |
|---|---:|---|
| M1 | v0.2.0 | Global users, organisation memberships, TEST/LIVE_LIKE isolation, versioned keys |
| M2 | v0.3.0 | Immutable statement imports and leased, versioned reconciliation evidence |
| M3 | v0.4.0 | Merchant accounts, posting-derived balances, deterministic settlement |
| M4 | v0.5.0 | Reserved payouts, numbered bank attempts, lookup-only ambiguous recovery |
| M5 | v0.6.0 | Versioned connectors, inbound webhooks, synthetic commerce synchronization |
| M6 | v0.7.0 | Frozen merchant API, admin API, cursor contracts, OpenAPI and Python SDK facade |
| M7 | v0.8.0 | Transactional audits, bounded telemetry, opt-in observability, measured failures |
| M8 | v0.9.0 | Environment-aware evidence console with permission and accessibility verification |
| M9 | v1.0.0 | Locally proven Cloudflare gateway, queues, rate coordination, immutable R2 ingress |

## Permanent gate

Every milestone runs Ruff and formatting, strict mypy, unit/integration/concurrency/security tests,
all Alembic drift checks, console lint/type/build/audit, relevant Playwright and axe journeys,
Compose validation, and the v0.1 lost-response proof. CI starts with empty databases and upgrades
the immediately previous release fixture to head before rerunning compatibility fixtures.

Release evidence contains the exact successful commit, tool versions, timestamps, commands, and
measured outputs. Failed or blocked runs are recorded separately. Each release includes notes,
migration guidance, limitations, and a runnable demonstration.

## Delivery constraints by milestone

- M1 backfills all current tenant financial and integration rows to TEST without changing event
  bytes or posting history; seeded keys become TEST version 1 keys.
- M2 deduplicates statement source plus digest and returns `STATEMENT_SOURCE_CONFLICT` when a
  source is reused with different bytes. Reconciliation claims use `SKIP LOCKED` leases.
- M3 never rewrites v0.1 journals; one deterministic opening journal transfers the legacy net
  payable position to pending payable.
- M4 uses `payout:{payout_id}:attempt:{n}` for one bank mutation. Ambiguity retains reservation;
  verified failure releases it; explicit retry creates the next attempt.
- M5 extracts connector protocols only after concrete payment and bank adapters are green.
- M6 uses stable `(createdAt,id)` opaque cursors with limits 1–100 and `INVALID_CURSOR` failures.
- M7 reports measured percentiles and failure behavior without inventing unsupported SLOs.
- M8 keeps login, Scenario Lab, and payment evidence primary and verifies keyboard, semantic
  tables, reduced motion, axe, 320px layout, and 200% zoom.
- M9 forwards synchronous merchant calls unchanged; queued payloads carry ingress digest and
  metadata only. Durable Objects never hold financial state. No owner account is deployed.
