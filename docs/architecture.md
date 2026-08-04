# Architecture

## Reliability boundary

RelayPay treats PostgreSQL as the only correctness authority. Redis and Celery accelerate work but
cannot create, erase, or finalize a financial outcome. All mutation commands follow the same
sequence: lock the payment, attach an idempotency record, persist a provider operation as `SENT`,
commit, perform HTTP, validate evidence, and enter the shared finalizer.

```mermaid
sequenceDiagram
    participant M as Merchant / console
    participant A as RelayPay API
    participant D as RelayPay PostgreSQL
    participant P as Mock provider
    participant W as Recovery poller
    participant R as Bundled receiver

    M->>A: Capture (idempotency key A)
    A->>D: Lock payment; attach key A; commit SENT
    A->>P: Mutate with stable provider key
    P->>P: Commit one capture effect
    P--xA: Response lost
    A->>D: Record ambiguous observation; schedule lookup
    W->>D: Claim expiring recovery lease
    W->>P: Signed status lookup (same stable key)
    P-->>W: Verified success
    W->>D: Shared finalizer: resource + journal + event + all keys
    W->>D: Materialize recipient delivery
    W->>R: HMAC-signed immutable event bytes
    R->>R: Deduplicate event ID + digest
    R-->>W: Acknowledge
    W->>D: Record immutable attempt; mark delivered
```

## Data ownership

- RelayPay database: organisations, sessions, API keys, payment resources, provider-operation
  observations, idempotency records, immutable journals/postings, merchant events, and delivery
  leases/attempts. It also owns exact statement bytes, immutable parsed items, leased
  reconciliation runs, matches, mismatch evidence versions, append-only workflow history,
  merchant accounts, settlement claims/items, and immutable balance transactions.
- Provider database: provider accounts, stable-key effects, deterministic one-shot faults, and
  immutable statement-export snapshots.
- Receiver schema: verified event IDs/digests and the deduplicated consumer effect.
- Browser: no durable authority and no credentials in local storage. It receives only bounded,
  redacted evidence through the session-authenticated API.

## Concurrency invariants

Payment lock precedes child/resource locks. A logical command has one provider operation and one
stable key. Refund availability is derived while holding the payment lock. Recovery and delivery
claims use `FOR UPDATE SKIP LOCKED`, opaque lease tokens, expiry, and idempotent finalization.
Reconciliation uses the same claim pattern; source imports serialize on a transaction-scoped
PostgreSQL advisory lock and `(environment, provider, source)` uniqueness.

Merchant balance commands lock the merchant before claiming captures. Balances are reconstructed
from debit/credit postings: capture credits pending payable, settlement debits pending and credits
receivable or available, and refunds debit pending/available or debit receivable. Balance
transactions are immutable journal-keyed projections; they never replace postings as authority.
No network operation occurs inside these accounting transactions.

Payout creation locks the merchant and appends a reservation without posting a journal. Each bank
attempt has a stable numbered key and immutable mutation-send evidence committed before HTTP. Once
sent, workers may only look up that key. Ambiguity retains the reservation; verified decline
releases it; verified success atomically consumes it with one payout journal, balance transaction,
terminal response, history record, and event.

Connector configuration is environment scoped and versioned. Credential ciphertext and digests are
immutable; a pending version is health-verified before explicit activation. Circuit and rate-limit
observations remain durable in PostgreSQL. Inbound webhook bytes are authenticated before parsing,
stored exactly once, then claimed in short transactions; commerce synchronization occurs outside
the RelayPay transaction against its separately credentialed database.

## Public surface

Caddy exposes the console, `/api/*`, `/health/*`, and the bundled `/webhooks/relaypay` receiver.
PostgreSQL, Redis, provider control routes, workers, and internal service ports are not published by
the production overlay.
## v0.11 agent runtime boundary

Business commands append immutable outbox rows in their PostgreSQL transaction. A lease publisher
commits its claim before Redpanda I/O and records acknowledgement in a second transaction. Consumers
deduplicate by `eventId`; workflow steps use reclaimable database leases, so broker or worker loss
cannot change truth. Model and tool calls likewise occur outside transactions, then write digest,
usage, pricing-version, and trace evidence under the exact lease token. See
[ADR-005](adr/005-postgresql-authoritative-agent-runtime.md).
