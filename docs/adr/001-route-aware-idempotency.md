# ADR-001: Route-aware idempotency and exact replay

Status: accepted

## Decision

Store only a peppered key digest and redacted hint. Bind each key to API version, method, route
template, normalized path parameters, and canonical request body. A compatible key attaches to the
singleton provider operation; terminal HTTP status and bytes are copied and replayed exactly.

## Consequences

Key reuse across payments, commands, or versions is a conflict rather than an accidental replay.
Multiple compatible capture keys converge on one operation and receive byte-identical terminal
responses. Key plaintext cannot be recovered from storage or evidence.
