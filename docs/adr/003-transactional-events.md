# ADR-003: Transactional events and leased delivery

Status: accepted

## Decision

The shared finalizer stores canonical immutable event bytes and snapshots active endpoint-version
recipients in the same local transaction as the resource outcome. PostgreSQL materializes delivery
rows, workers claim them with expiring tokens, and every attempt signs timestamp plus stored bytes.

## Consequences

There is no state/event split-brain and endpoint edits cannot rewrite history. Delivery is
at-least-once, so the bundled receiver deduplicates event ID plus digest. Redis loss affects latency,
not durable progress.
