# ADR 005: PostgreSQL-authoritative agent runtime

Status: Accepted

## Context

Agent workflows cross Kafka, Celery, model providers, and allowlisted read tools. Each dependency can
lose, duplicate, delay, or ambiguously acknowledge work. Financial and approval truth cannot depend
on any of them.

## Decision

PostgreSQL is authoritative for business-event outbox rows, consumed-event deduplication, immutable
workflow/prompt/pricing versions, runs, reclaimable step leases, approvals, artifacts, traces, budgets,
and dead letters. Redpanda is a derived at-least-once stream. Celery is dispatch only. Network I/O is
performed only after the transaction that claims work commits; completion uses a new transaction and
the exact lease token.

Models receive delimited, tokenized evidence and may call only versioned tenant-scoped read tools.
Refusals, policy failures, and invalid schemas are terminal; only transport, rate-limit, timeout, and
provider 5xx failures may route OpenAI to Claude to Gemini. Artifact and model budgets fail closed.

## Consequences

Broker and worker recovery is deterministic and independently testable. Duplicate delivery is safe.
PostgreSQL bears additional bounded evidence storage, and every future agent must use these common
leases, approval hashes, tool boundaries, and trace records rather than inventing another authority.
