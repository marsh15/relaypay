# 03 — App Flow and Journey Map

## Actors

- **Logged-out visitor:** can only see Login and service availability messaging.
- **Organisation administrator:** uses the three-page console and administrative evidence/retry actions for their own organisation.
- **Merchant API client:** creates resources and invokes payment commands through scoped API keys.
- **Internal worker/scheduler:** advances committed recovery, materialization, and delivery work using internal identifiers.
- **Mock provider:** records one mutation per stable key and returns signed results/lookups.
- **Webhook receiver:** validates signatures and deduplicates event IDs.

## Console Routes

| Route | Access | Purpose |
|---|---|---|
| `/login` | public | Authenticate one of the seeded organisation administrators. |
| `/lab` | admin session | Run deterministic scenarios and show their progress/result. |
| `/payments/[paymentIntentId]` | same-org admin | Inspect the complete bounded evidence graph for one payment. |

Standalone resource lists, ledger pages, delivery pages, and review queues are not routes. Their detail is folded into the payment evidence page.

## API Route Map

### Sessions

- `POST /api/session/login`: credentials + CSRF bootstrap → opaque session cookie.
- `POST /api/session/logout`: valid session + CSRF → revoke server session and clear cookie.
- `GET /api/session/me`: current principal and organisation-safe display data.

### Session-Only Demo Scenarios

- `POST /api/demo/scenarios`: start one allowlisted synthetic scenario and return its run/correlation ID.
- `GET /api/demo/scenarios/{scenario_run_id}`: poll bounded scenario progress, invariant assertions, and resulting payment ID.

These routes require an administrator session and CSRF for creation. They are not merchant routes and are excluded from the financial idempotency fingerprint protocol.

### Merchant Commands and Reads

- `POST /api/v1/customers`
- `POST /api/v1/payment_intents`
- `GET /api/v1/payment_intents/{payment_intent_id}`
- `POST /api/v1/payment_intents/{payment_intent_id}/authorize`
- `POST /api/v1/payment_intents/{payment_intent_id}/capture`
- `POST /api/v1/payment_intents/{payment_intent_id}/refunds`
- `GET /api/v1/operations/{operation_id}`

### Administrative Evidence and Recovery

- `GET /api/v1/payment_intents/{payment_intent_id}/evidence`
- `POST /api/v1/operations/{operation_id}/retry_lookup`
- `GET /api/v1/webhook_deliveries/{delivery_id}`
- `POST /api/v1/webhook_deliveries/{delivery_id}/replay`

## Navigation

- Desktop uses a compact top bar with RelayPay wordmark, organisation badge, environment badge, and logout action.
- Primary navigation contains only **Scenario Lab**. Payment Evidence is contextual and reached from a scenario result or copied same-tenant URL.
- Payment Evidence includes a visible back link to Scenario Lab and a copyable payment ID.
- Mobile retains the top bar, collapses account actions into a menu, and uses the browser/back-link pattern; no bottom tab bar is needed for two authenticated destinations.
- Deep links preserve the destination through login only when the target is a safe same-origin path.

## First Screen and Redirect Rules

| Condition | Result |
|---|---|
| Visit `/` while logged out | redirect to `/login` |
| Visit `/` with valid admin session | redirect to `/lab` |
| Visit authenticated route without/with expired session | redirect to `/login?next=<safe-path>` |
| Visit `/login` with valid session | redirect to `/lab` |
| Login succeeds with safe `next` | redirect to `next`; otherwise `/lab` |
| Access another tenant's payment | show generic not-found state; never reveal ownership |
| Merchant API key calls evidence/admin route | return `403`; no console redirect |
| Logout succeeds | clear local sensitive UI state and redirect to `/login` |

## Authentication Flow

1. Visitor arrives at `/login`.
2. The page fetches or receives a CSRF bootstrap token without exposing a session secret.
3. Visitor submits seeded demo credentials.
4. Submit control enters a pending state and duplicate submission is disabled.
5. Server rate-limits, validates credentials, creates an opaque server-side session, rotates CSRF state, and sets the secure cookie.
6. UI routes to the safe `next` destination or `/lab`.
7. `GET /api/session/me` supplies display name and organisation label, not authorization truth for later backend calls.
8. Logout revokes the session server-side and clears the cookie.

Failure states:

- Invalid credentials: inline generic error; do not disclose which field was wrong.
- Rate limited: show wait duration from `Retry-After` and retain the identifier field.
- Service unavailable: show a retry action and readiness hint without infrastructure secrets.

## Core Journey 1 — Lost Capture Response

**Goal:** prove a provider-side capture completed once even though RelayPay lost the mutation response.

1. Admin opens `/lab` and selects **Lost capture response**.
2. The lab explains the injected fault and expected invariant before execution.
3. Admin selects **Run scenario**.
4. Backend creates customer/payment, authorizes it, configures the one-shot provider fault, and initiates capture using stable keys.
5. Scenario stepper shows `Setup → Authorized → Capture sent → Response lost → Lookup → Finalized → Delivered`.
6. Capture returns/appears `PROCESSING`; the page polls the scenario/evidence using bounded backoff.
7. Recovery looks up the stable key and the shared finalizer applies verified success.
8. The scenario summary asserts counts: one provider effect, capture, journal, event, and expected delivery.
9. The runner replays every bound idempotency key and asserts identical terminal response bytes.
10. Admin selects **Inspect evidence** and lands on `/payments/[paymentIntentId]`.

If progress exceeds the scenario timeout, the run becomes **Needs inspection**, not failed financial truth, and links to available evidence.

## Core Journey 2 — Concurrent Refund Reservation

**Goal:** prove concurrent requests cannot over-refund a captured payment.

1. Admin selects **Concurrent refund boundary** and reviews captured amount plus requested competing refunds.
2. Admin runs the scenario.
3. Backend creates and captures a payment, then releases simultaneous refund commands.
4. Each command locks the same payment before calculating availability.
5. The UI shows which refund reserved value and which request received a legal-state conflict.
6. Successful provider results finalize through the shared finalizer and create balanced refund journals/events.
7. Evidence view shows captured, succeeded, processing, review, and currently available amounts as a reconciliation equation.

## Core Journey 3 — Idempotent Retry and Compatible Attachment

**Goal:** prove different compatible keys attach to one singleton operation and converge on one response.

1. Merchant/scenario creates a payment and sends authorization or capture with key A.
2. Before completion, the same canonical command is sent with key B.
3. The initiation transaction attaches key B to the existing operation and returns its `202` envelope.
4. Finalization locks all attached records by ID and stores one canonical terminal result.
5. Replays of A and B return the stored status/body with `Idempotency-Replayed: true`.
6. Evidence displays fingerprints safely, target operation, attachment time, and a byte-equality assertion.

## Payment Evidence Page

The page fetches one organisation-scoped, bounded evidence read model and renders:

1. **Proof summary:** current lifecycle state, invariant badges with text/icons, amount, refundable equation, and correlations.
2. **Lifecycle timeline:** payment, authorization, capture, refunds, provider send/lookups, and final outcome.
3. **Idempotency:** redacted key labels/digests, fingerprint summaries, targets, statuses, and replay equality.
4. **Provider evidence:** stable key, sanitized canonical request summary, attempts, signed response classification, and review history.
5. **Ledger:** journal references and debit/credit postings with a balanced-total assertion.
6. **Events and delivery:** immutable event ID/type/bytes digest, captured endpoint versions, deliveries, leases, retry attempts, and receiver dedup status.
7. **Raw safe evidence:** copyable sanitized JSON in a collapsed disclosure for technical reviewers.

The page never exposes API key material, session identifiers, peppers, signing secrets, webhook secrets, cookies, CSRF values, or raw sensitive headers.

## Review Lookup Flow

1. An operation displays `REQUIRES_REVIEW` with reason and last validated evidence.
2. Admin expands the provider evidence section.
3. **Retry lookup** is available only if no active lookup lease exists.
4. A confirmation dialog states that the action queries status and never repeats the mutation.
5. On confirmation, the API schedules/acquires lookup work and returns the current operation envelope.
6. UI polls until state changes or a bounded timeout is reached.
7. Verified success/failure finalizes; continued uncertainty remains review with new evidence appended.

No manual success/failure control exists.

## Webhook Replay Flow

Webhook replay is a presentation-cut feature. If implemented:

1. Admin opens a delivery row inside Payment Evidence.
2. **Replay delivery** opens a confirmation dialog identifying event and immutable endpoint version.
3. Backend creates a replay execution linked to the same stored event bytes and recipient.
4. UI appends the new attempt/result; original attempt history is unchanged.

## Empty States

- Scenario Lab with no runs: show the primary lost-response scenario and a short explanation; no empty dashboard cards.
- Payment with no authorization: timeline shows the next legal action, not a blank section.
- No capture journal yet: ledger section states that authorization creates no journal.
- Event not yet materialized: show committed event and “delivery row pending materialization.”
- No refunds: show captured/refundable equation and “No refunds requested.”
- No review history: omit the section.

## Loading States

- Route load: retain page shell and use labeled skeletons for evidence groups.
- Scenario execution: show persistent step progress, elapsed time, and a cancel-view action; leaving the page does not cancel backend work.
- Evidence refresh: keep old evidence visible, mark it “refreshing,” and replace atomically.
- Button mutations: disable only the initiating control, preserve its width, and display a text status for assistive technology.

## Error and Recovery States

- Validation error: field-level message plus error code when useful.
- Idempotency conflict: explain that the key belongs to different canonical input; never invite blind retry with the same key.
- Legal-state conflict: show current durable state and permitted next action.
- Cross-tenant/absent: one generic not-found page.
- Temporary dependency failure: preserve entered safe data, show `Retry-After`, and allow retry.
- Polling/network interruption: keep the known committed state visible and offer reconnect; never label the financial operation failed.
- Malformed/contradictory provider evidence: show review state and reason, not a decline.
- Dead-letter delivery: show attempt count, last safe error class, and replay action only when implemented.

## Modals, Drawers, and Overlays

- Confirmation dialog: retry lookup and optional webhook replay only.
- Mobile evidence navigation: a sheet listing evidence sections with anchored links.
- Tooltips: definitions for stable key, reservation, outbox, lease, and response-byte equality; all are keyboard accessible.
- No modal is used for payment evidence itself or for long-form error content.
