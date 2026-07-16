# 01 — Product Requirements Document

## App Name

RelayPay

## Tagline

An evidence-first payment-orchestration sandbox that proves one financial effect under retries, races, crashes, and ambiguous provider responses.

## Problem

Payment integrations often look correct on the happy path but fail when a client retries, two commands race, a worker crashes after committing state, or a provider completes an operation while its response is lost. Those failures can create duplicate provider effects, over-refunds, unbalanced accounting, missing events, unstable replays, or cross-tenant data leaks.

RelayPay provides a small, inspectable system that demonstrates how to prevent those failures. It is a portfolio and learning product, not a production payment processor.

## Target User

The primary user is a backend or fintech engineer, technical reviewer, or hiring panel evaluating correctness under failure. They want to trigger deterministic scenarios and inspect the payment, provider-operation, ledger, event, and delivery evidence without reading the entire codebase first.

A secondary user is an API client acting as a merchant inside one seeded organisation. The client creates synthetic customers and payment intents, executes the payment lifecycle, retries safely, and receives signed webhook events.

## Core Value Proposition

RelayPay turns difficult distributed-payment claims into observable, testable evidence. A reviewer can see stable provider keys, idempotency binding, immutable balanced journals, transactional events, recovery attempts, and deduplicated webhook delivery for the same payment.

## Product Goals

- Prove one provider mutation and one local financial transition for each logical authorization, capture, or refund.
- Preserve a stable response across retries and compatible idempotency keys.
- Recover ambiguous provider outcomes without repeating a mutation request.
- Prevent concurrent refunds from reserving more than the captured amount.
- Record one immutable balanced journal for each successful capture or refund.
- Persist merchant events with the financial transition and deliver them at least once.
- Make all evidence inspectable while maintaining organisation isolation.
- Ship a reproducible demonstration in six weeks.

## Core Lifecycle

```text
Customer
→ Payment intent
→ Authorization
→ One full capture
→ Immutable balanced ledger
→ Transactional outbox
→ Signed webhook with retry
→ Concurrent-safe partial/full refunds
```

## Must-Have Features

### Tenant and Access Boundary

- Seed two organisations, administrators, merchant API keys, webhook endpoint versions, and demo data.
- Authenticate console users with opaque server-side sessions.
- Authenticate merchant API calls with API keys stored as prefix plus peppered digest.
- Scope every user-facing lookup by organisation; another tenant's resource appears absent (`404`).
- Restrict evidence reads, review lookup, and delivery replay to administrator sessions.

### Customer and Payment Creation

- Create customers with unique `(organisation_id, merchant_customer_reference)`.
- Create INR payment intents with positive integer paise amounts.
- Require structured, route-aware idempotency for payment creation.
- Correctly distinguish a same-key race from a different-key/same-merchant-reference race.

### Authorization, Capture, and Refunds

- Support one authorization and one full capture per payment intent.
- Support multiple partial or full refunds up to the captured amount.
- Attach compatible keys to an existing singleton authorization or capture in any operation state.
- Reserve refund value while a refund is `PROCESSING` or `REQUIRES_REVIEW`.
- Release a reservation only after verified provider failure.
- Return the same canonical terminal HTTP status and response bytes to every attached idempotency key.

### Provider Ambiguity and Recovery

- Use one separate HTTP mock-provider application and database.
- Store canonical request bytes and commit a `SENT` attempt before network I/O.
- Never repeat a provider mutation after any recorded send.
- Query provider status using a stable provider key after ambiguous results.
- Treat only a validated, matching provider business decline as financial `FAILED`.
- Move contradictory, malformed, unsigned, mismatched, or persistently indeterminate results to `REQUIRES_REVIEW`.
- Allow review to inspect evidence and retry lookup; never allow an administrator to invent an outcome.

### Ledger and Events

- Create no journal for authorization.
- Post exactly one balanced journal for a successful capture or refund.
- Keep posted journals and postings immutable; corrections require compensating journals.
- Create one immutable merchant event during finalization.
- Snapshot immutable webhook endpoint-version recipients at event creation.
- Materialize and deliver webhooks with leases, immutable attempt history, bounded retry, and dead-letter state.
- Sign stored event bytes with HMAC-SHA256 and support receiver deduplication by event ID.
- Keep PostgreSQL as the source of progress if Redis is unavailable.

### Evidence and Demonstration

- After the week-four backend gate passes, provide the committed three-page console: Login, Scenario Lab, and Payment Evidence.
- Make payment state, provider attempts, recovery history, journals, events, recipients, deliveries, and correlations inspectable.
- Provide a CLI demonstration for the primary lost-response scenario.
- Emit structured JSON logs with request, payment, and operation correlation IDs.
- Provide liveness and readiness endpoints.

The CLI proof is mandatory in every release. The console is the preferred presentation surface but may be cut in whole or part under the documented cut policy; a late console never blocks or displaces backend correctness.

## Nice-to-Have Features

These are permitted only after all release gates are green:

- Additional visual polish beyond an accessible functional console.
- Browser automation beyond one lost-response smoke test.
- Console controls for webhook replay.
- Hosted public sandbox.
- Concise demo video.

## Explicitly Out of Scope

- Real money, production provider connectors, or sensitive identity data.
- Currencies other than INR, FX, fees, settlement, payouts, stored balances, or wallets.
- Partial or multiple captures.
- Disputes, reconciliation, inbound provider webhooks, or general event sourcing.
- Public signup, self-service API key management, or webhook configuration UI.
- Kafka, Kubernetes, microservices, PostgreSQL RLS, or distributed exactly-once claims.
- OpenTelemetry, Prometheus/Grafana, k6, and general resource list screens.
- Standalone review, ledger, delivery, or webhook administration pages.

## User Stories

- As a merchant API client, I want to retry a timed-out command with the same key so that I receive the same result without creating another effect.
- As a merchant API client, I want conflicting reuse of an idempotency key rejected so that unrelated commands cannot alias one another.
- As a merchant API client, I want concurrent refund requests serialized so that refunds never exceed captured value.
- As a reviewer, I want to trigger a lost provider response so that I can see status-only recovery complete the operation once.
- As a reviewer, I want all attached keys to expose identical terminal bytes so that stable replay is directly verifiable.
- As a reviewer, I want to inspect journals and postings so that I can verify every financial transition is balanced and immutable.
- As a reviewer, I want to inspect event bytes and delivery attempts so that I can verify commit-before-delivery and at-least-once behavior.
- As an organisation administrator, I want another tenant's identifier to return `404` so that tenant existence and data are not disclosed.
- As an operator, I want ambiguous or contradictory evidence placed in review so that transport failures are never misreported as business declines.
- As an operator, I want expired work leases reclaimed so that a worker crash cannot permanently strand committed work.

## Acceptance Contract

RelayPay is releasable only when the Scenario Lab or CLI proves:

```text
lost provider response
→ PROCESSING
→ status lookup
→ one capture
→ one journal
→ one event
→ webhook delivery
→ stable replay
```

The proof must run against real PostgreSQL and Redis, not mocks of persistence or broker behavior.

## Success Metrics

- 100% of mandatory correctness tests pass against PostgreSQL and Redis.
- The primary demonstration creates exactly one provider capture effect, capture row, journal, merchant event, and delivery row for each captured endpoint version.
- Every idempotency key bound to the same operation returns byte-identical terminal content and the recorded status.
- Concurrent refund tests never permit reserved plus succeeded refunds to exceed captured amount.
- All cross-tenant user paths in the test matrix return `404`; evidence access by merchant API key returns `403`.
- Redis outage tests show PostgreSQL polling eventually resumes materialization, recovery, and delivery work.
- A fresh clone can start, migrate, seed, run the core demonstration, and run tests using documented commands.
- The console meets keyboard, focus, contrast, and responsive acceptance checks if it passes the week-four go/no-go gate.
