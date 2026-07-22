# RelayPay Phase 2 product contract

Status: implementation contract for v0.2.0 through v1.0.0. The six documents under
`docs/vibe-coding/` remain the unchanged v0.1 brief.

## Product promise

RelayPay is a synthetic, evidence-first payment orchestration sandbox. Phase 2 adds isolated
TEST and LIVE_LIKE environments, reconciliation, ledger-backed merchant balances, settlements,
payouts, versioned connectors, public contracts, operational evidence, an expanded console, and
a locally proven Cloudflare edge extension.

No release may weaken the v0.1 lost-response guarantee: after any recorded provider send,
recovery is lookup-only; terminal state, ledger, events, provider evidence, and idempotency output
finalize atomically and exactly once.

## Authority and boundaries

- PostgreSQL is the sole financial and durable workflow authority. Redis, Celery, Queues,
  Durable Objects, and caches may accelerate work but cannot decide financial truth.
- Network I/O never occurs inside a database transaction.
- A merchant API key resolves exactly one organisation and environment. Headers cannot override
  that environment. Operator environment paths must belong to the selected organisation.
- Every environment-scoped read predicates on both organisation and environment; cross-boundary
  reads return `404`.
- Existing `/api/v1` wire bytes, statuses, error envelope, idempotent replays, and single-resource
  reads remain compatible.
- Operator commands use secure sessions, CSRF, permission checks, and route-aware idempotency.

## Identity and permission contract

Users are global and carry only a platform role. Organisation membership carries one of
`ORGANISATION_ADMIN`, `DEVELOPER`, or `VIEWER`. Platform administrators can provision
organisations but need membership before financial access. Administrators manage operational
commands; developers read evidence and use assigned keys; viewers are read-only.

Every organisation has one TEST and one LIVE_LIKE environment. The v0.1 dataset migrates
deterministically to TEST; LIVE_LIKE begins empty.

## Financial contract

Journals and postings are immutable and balanced. Merchant balances are derived from postings
plus active payout reservations, never mutable counters. New captures credit pending payable;
settlement offsets receivable before moving value to available payable. Refunds draw from the
payment's unsettled pending value, then available payable, then merchant receivable. Payout
reservations do not post journals; only verified success posts available payable to payout
clearing exactly once.

## Evidence and workflow contract

Provider evidence, event and webhook bytes, recipient snapshots, statements and parsed items,
mismatch evidence versions, inbound payloads, audit records, balance transactions, and connector
versions are append-only. Mutable workflow rows change only through validated transitions with
append-only history. Evidence refresh may explain or link an existing compensating journal but
may not invent outcomes or rewrite financial/provider history.

## Security and data contract

Secrets are shown once at creation, stored as digests or encrypted versions as appropriate, and
never logged or returned to browsers. Exact inbound bytes are signature-checked before parsing,
digested, replay-window checked, and deduplicated. Errors retain
`{error:{code,message,details}}` without secrets or internal exception text.

All credentials and data are synthetic and unsuitable for real financial use. Internal payment
and ledger authority is INR-only; an imported statement may preserve another uppercase
three-letter currency solely to report a reconciliation mismatch and never to perform FX. Phase
2 does not add PCI claims, real money, FX, disputes, inventory sagas, Kafka, RabbitMQ,
Kubernetes, microservice decomposition, multi-region operation, hosting, or owner-account
deployment.
