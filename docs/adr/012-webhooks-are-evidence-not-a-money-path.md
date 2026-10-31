# ADR-012: Webhooks are evidence, not a money path

Status: accepted (v2.0, M17; the ordering decision it depends on is pending)

## Context

M17 accepts signed PSP webhooks so that RelayPay learns about provider outcomes without waiting for
its next poll. The v2.0 rule is that only the one finalizer moves money (CONTRIBUTING, invariant
3), and that a provider mutation is recorded before its HTTP call and never resent (ADR-002). A
webhook arrives outside that protocol. It can be duplicated, delayed, reordered, or forged in
principle, and it may name an operation that RelayPay has never seen.

## Decision

1. **Verify before parsing, then store once.** `POST /api/inbound/v1/psp/{provider}` checks the
   timestamp window and the HMAC over the exact bytes before the body is parsed. A verified event
   is stored raw in `psp_events` once per `(provider, provider_event_id)`. A redelivery of the same
   bytes is a replay. The same id with different bytes is a conflict (`409 PSP_EVENT_CONFLICT`).
   The row stores the stable key and the matched operation, if any. An event that names no known
   operation is kept as `UNMATCHED`, never dropped.

2. **A webhook is evidence, recorded as a provider attempt.** The applier turns an applied event
   into a `WEBHOOK` row in `provider_attempts` and finalizes the operation through
   `finalize_verified_attempt`. That is the only code path that changes the operation's terminal
   state. The webhook never writes a ledger journal or a payment status directly.

3. **Polling stays the guarantee.** A missed, dropped, duplicated, or late webhook never changes
   correctness. Lookups under ADR-002 and ADR-011 still resolve every operation. Webhooks only make
   that resolution faster.

4. **Ordering is decided per operation, from its current state.** Whether an event applies, is
   ignored as a duplicate or stale report, or goes to review is a function of the operation's
   status and the event outcome (`packages/relaypay/psp/ordering.py::decide`). The specification is in
   its docstring. The decision is hand-written by the maintainer, so the applier's dependency is
   an interface and a `NotImplementedError` today. Until `decide` exists, every event that matches an
   operation is recorded `DEFERRED` with `ORDERING_DECISION_UNAVAILABLE`, and the operation does
   not change.

5. **Evidence that contradicts the operation's identity goes to review.** An event whose effect
   does not match the operation's account, stable key, kind, reference, amount, or currency is
   recorded `REVIEW`, and the operation moves to `REQUIRES_REVIEW`, the same as a mismatched lookup.

## Consequences

- A webhook cannot reach the ledger except through the shared finalizer, with the evidence attempt
  and its digest recorded. Recovery accounting changed to count only `LOOKUP` attempts toward
  the five-lookup review budget; mutation and webhook attempts do not consume that budget.
- Until the ordering decision lands, PSP events do not change operation state. Operations resolve by
  polling, as they did before M17. Webhook acceptance is a durable record, not a fast path.
- Events that stay `DEFERRED` are not re-leased by the applier, which claims only `PENDING` and
  lapsed `PROCESSING` rows. Applying them after `decide` lands needs a deliberate step that does
  not exist yet.
- Only the simulator provider is accepted at the inbox today. The Razorpay and Cashfree adapters
  are parsers and verifiers, and they are not routed into the inbox, so they cannot change state yet.
- A PSP webhook is authenticated with `PROVIDER_WEBHOOK_SECRET`, which is separate from the signing
  key for API responses, so a leaked webhook secret does not forge API responses.
