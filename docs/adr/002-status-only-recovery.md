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

## Implementation note (v1.0.1)

`SENT` is the state of the operation's first `MUTATION` attempt (`provider_attempts.state`), set
together with `last_sent_at` and the stored request bytes; the operation's own status stays
`PROCESSING` until finalization. The lookup request is unsigned (stable key and account id); the
signature that matters is on the provider's response, which RelayPay verifies before acting.
