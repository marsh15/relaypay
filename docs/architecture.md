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
  leases/attempts.
- Provider database: provider accounts, stable-key effects, and deterministic one-shot faults.
- Receiver schema: verified event IDs/digests and the deduplicated consumer effect.
- Browser: no durable authority and no credentials in local storage. It receives only bounded,
  redacted evidence through the session-authenticated API.

## Concurrency invariants

Payment lock precedes child/resource locks. A logical command has one provider operation and one
stable key. Refund availability is derived while holding the payment lock. Recovery and delivery
claims use `FOR UPDATE SKIP LOCKED`, opaque lease tokens, expiry, and idempotent finalization.

## Public surface

Caddy exposes the console, `/api/*`, `/health/*`, and the bundled `/webhooks/relaypay` receiver.
PostgreSQL, Redis, provider control routes, workers, and internal service ports are not published by
the production overlay.
