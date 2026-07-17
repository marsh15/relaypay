# ADR-002: Status-only recovery after a recorded send

Status: accepted

## Decision

Commit the provider operation as `SENT` before mutation HTTP. After any recorded send, every retry
is a signed provider status lookup using the same stable key; the mutation endpoint is never called
again. Ambiguous or invalid evidence stays recoverable or enters `REQUIRES_REVIEW`.

## Consequences

RelayPay cannot duplicate a provider financial effect to improve availability. Recovery needs a
provider lookup contract and durable expiring leases. Timeouts, resets, 5xx, malformed bodies, and
contradictions never become financial declines.
