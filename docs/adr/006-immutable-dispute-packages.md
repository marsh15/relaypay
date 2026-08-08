# ADR 006: Immutable, digest-bound dispute packages

Status: accepted for v0.12.0.

RelayPay stores every draft as a new PostgreSQL row and freezes each submission package into exact
ZIP bytes with a canonical manifest, per-file SHA-256 digests, and an HMAC signature. Approval binds
the package digest, not a mutable draft. Editing evidence or response text invalidates prior packages
and approvals; approved bytes are never regenerated or replaced.

Submission persists a stable key and `SENT` state before network I/O. A lost response becomes
`AMBIGUOUS`; recovery performs status lookup only and never repeats the mutation. PostgreSQL remains
authoritative. The synthetic network is an adapter, not a source of case or approval truth.

This costs database capacity (packages are capped at 20 MiB and attachments at 5 MiB) but provides
byte-stable replay, auditable maker-checker control, and a defensible one-external-effect invariant.
