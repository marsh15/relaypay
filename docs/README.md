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
| [operations/observability.md](operations/observability.md) | Opt-in metrics, traces, dashboard, and what they do and do not cover |
| [operations/resilience-proofs.md](operations/resilience-proofs.md) | Requirement-to-test map for the failure properties |
| [operations/cloudflare-edge.md](operations/cloudflare-edge.md) | The optional Cloudflare Worker (source and local proof only) |
| [threat-model.md](threat-model.md) | Trust boundaries, controls, and residual risk |
| [limitations.md](limitations.md) | Non-goals and known gaps, including documentation inconsistencies |
| [adr/](adr/README.md) | Ten architecture decision records |
| [../CONTEXT.md](../CONTEXT.md) | Domain glossary |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Conventions, invariants, schema and API change procedures |

## Release history

Per-release records. They describe the release as shipped and are not edited afterwards.

| Directory | Contents |
| --- | --- |
| [releases/](releases/) | Release notes, `v0.1.0` to `v1.0.1` |
| [migrations/](migrations/) | Upgrade guides for each release that needs one |
| [evidence/](evidence/) | Measured gate evidence for `v0.8.0` to `v1.0.1` |
| [test-evidence.md](test-evidence.md) | The release gate and the `v0.1.0` evidence record |

## Historical design records

These are frozen. They are kept for context and disagree with the current system in places.

| Document | Status |
| --- | --- |
| [vibe-coding/](vibe-coding/README.md) | The original Phase 1 PRD, TRD, flows, UI brief, schema, and plan. Describes Celery and Redis (removed in v1.0.1) and a two-database layout |
| [week-4-gate.md](week-4-gate.md) | The Week 4 delivery gate record (2026-07-17) |
| [phase-2/](phase-2/product-contract.md) | The Phase 2 product contract and milestone roadmap. Its milestone-to-release table predates the shipped v1.0.0 and v1.0.1 scope |
