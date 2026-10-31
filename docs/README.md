# Documentation index

## Current reference

| Document | Contents |
| --- | --- |
| [architecture.md](architecture.md) | Components, ports, poller schedule, the lost-response flow, data ownership, concurrency rules, outbox and model boundaries |
| [diagrams/](diagrams/) | `architecture.svg` (rendered) and `architecture.excalidraw` (editable source) |
| [database.md](database.md) | Databases and roles, migration chains, tables by domain, ledger, constraints, immutability, seed and reset |
| [api/reference.md](api/reference.md) | Routes, authentication, idempotency, pagination, webhooks, simulator endpoints |
| [api/error-catalog.md](api/error-catalog.md) | Error envelope and codes |
| [api/python-sdk.md](api/python-sdk.md) | SDK facade, examples, and their local overrides |
| [configuration.md](configuration.md) | Every environment variable, defaults, production validation, known gaps |
| [deployment.md](deployment.md) | Single-host Ubuntu sandbox: configure, start, back up, reset |
| [operations/runbook.md](operations/runbook.md) | Operator procedures for PSP inbox statuses, review, webhook dead letters, the outbox, and chaos runs |
| [operations/observability.md](operations/observability.md) | Opt-in metrics, traces, dashboard, and what they do and do not cover |
| [operations/resilience-proofs.md](operations/resilience-proofs.md) | Requirement-to-test map for the failure properties, and what is not yet proven |
| [operations/cloudflare-edge.md](operations/cloudflare-edge.md) | The optional Cloudflare Worker (source and local proof only) |
| [../packages/relaypay_reconcile/README.md](../packages/relaypay_reconcile/README.md) | Standalone, offline settlement/order reconciliation, formats and exact exception definitions |
| [../loadtest/README.md](../loadtest/README.md) | Reproducible synthetic HTTP workloads, machine context and measurement limits |
| [rca/](rca/README.md) | Root-cause analyses for the defects the v2.0 proof harness found (F1 to F4) |
| [threat-model.md](threat-model.md) | Trust boundaries, controls, and residual risk |
| [limitations.md](limitations.md) | Non-goals and known gaps, including verification status and documentation inconsistencies |
| [adr/](adr/README.md) | Twelve architecture decision records (ADR-001 to ADR-012) |
| [../CONTEXT.md](../CONTEXT.md) | Domain glossary |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Conventions, invariants, schema and API change procedures |

## Release history

Per-release records. A shipped release is not edited afterwards. The `v2.0.0-m17` note is a draft
for work still in progress, so it is revised as M17 lands. No final v2.0 release note exists yet.

| Directory | Contents |
| --- | --- |
| [releases/](releases/) | Release notes, `v0.1.0` to `v1.0.1`, the v2.0 milestone notes `v2.0.0-m15` and `v2.0.0-m16`, and the `v2.0.0-m17` draft |
| [migrations/](migrations/) | Upgrade guides for each release that needs one |
| [evidence/](evidence/) | Measured gate evidence for `v0.8.0` to `v1.0.1` |
| [evidence/2026-10-31.md](evidence/2026-10-31.md) | Current completion-pass measurements, recovery recording, verification and unresolved v2 boundaries |
| [test-evidence.md](test-evidence.md) | The release gate and the `v0.1.0` evidence record |

## Historical design records

These are frozen. They are kept for context and disagree with the current system in places.

| Document | Status |
| --- | --- |
| [vibe-coding/](vibe-coding/README.md) | The original Phase 1 PRD, TRD, flows, UI brief, schema, and plan. Describes Celery and Redis (removed in v1.0.1) and a two-database layout |
| [week-4-gate.md](week-4-gate.md) | The Week 4 delivery gate record (2026-07-17) |
| [phase-2/](phase-2/product-contract.md) | The Phase 2 product contract and milestone roadmap. Its milestone-to-release table predates the shipped v1.0.0 and v1.0.1 scope |
