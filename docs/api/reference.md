# HTTP API reference

The API is a FastAPI application (`apps/api`, port 8000). The current OpenAPI document is
[`contracts/openapi/v2.0.0.json`](../../contracts/openapi/v2.0.0.json) (`info.version` `2.0.0`).
It describes 84 operations on 75 paths and is checked against the code by `make api-contract`.
`/api/v1` is frozen: every v1 operation must stay byte-identical to the
[`api-v1-v0.6.0.json`](../../contracts/openapi/api-v1-v0.6.0.json) baseline.
[`v0.9.0.json`](../../contracts/openapi/v0.9.0.json) is the v1.0.x document, kept for history. `/metrics` is deliberately excluded from it. This page
summarizes the surface; the OpenAPI document is the contract for request and response shapes.

All amounts are integer paise in `INR`. Every response includes `X-Request-ID` (the inbound value is
echoed, otherwise generated). Errors use `{"error": {"code", "message", "details"}}`; see the
[error catalog](error-catalog.md). Request bodies are capped per service (2 MiB on the API); see
[request size](error-catalog.md#request-size).

## Authentication

| Surface | Credential | Notes |
| --- | --- | --- |
| Merchant API `/api/v1/customers`, `payment_intents*`, `operations/{id}` | `Authorization: Bearer rpk_{test\|live_like}_<prefix>.<secret>` | The key is bound to one environment and carries scopes. Missing scope returns `403 FORBIDDEN`. |
| Admin API `/api/admin/v1/*`, `/api/demo/*`, evidence, retry, delivery replay | Session cookie `relaypay_session` | Bearer-only requests get `403 ADMIN_SESSION_REQUIRED`. Every write also needs `X-CSRF-Token`. |
| Inbound events `/api/inbound/v1/connectors/{id}/events` | HMAC headers | See [below](#inbound-connector-events). |
| `/health/*` | none | |
| `/metrics` | none | `404` unless `OBSERVABILITY_ENABLED=true`. Keep it network-internal. |

**API keys.** Format `rpk_test_<8 base32>.<43 url-safe chars>` (`rpk_live_like_...` for the other
environment). Only a peppered HMAC-SHA256 digest and the public prefix are stored. A key has
versions: rotation creates a `PENDING` version, activation promotes it and revokes the previous
active one, and revocation revokes all versions. The secret is returned once, at creation or
rotation. Seeded keys carry `customers:write`, `payments:read`, `payments:write`.

**Sessions.** `POST /api/session/login` takes `{email, password, organisationId?}`. Passwords are
verified with Argon2id; an unknown email costs the same verification time. A user in several
organisations must pass `organisationId` (otherwise `409 ORGANISATION_CONTEXT_REQUIRED`). The
cookie is HttpOnly, `SameSite=Lax`, path `/`, eight hours, and `Secure` when
`APP_ENV=production`. The login response and `GET /api/session/me` return a `csrfToken`, an HMAC of
the session id, to send as `X-CSRF-Token`. Login is limited to 10 requests per 60 seconds per
client address (first `X-Forwarded-For` entry when present), per API process.

**Roles and scopes** (membership role to scopes):

| Role | Scopes |
| --- | --- |
| `ORGANISATION_ADMIN` | `admin`, `members:write`, `keys:write`, `operations:write` |
| `DEVELOPER`, `VIEWER` | `technical:read`, `financial:read` |
| `OPERATIONS_ANALYST` | the two above plus `workflows:write` |
| `APPROVER` | the above plus `approvals:write` |

`PLATFORM_ADMIN` (a user attribute, not a membership role) is required only to create
organisations. Cross-tenant and cross-environment identifiers return `404 RESOURCE_NOT_FOUND`.

## Idempotency

Financial commands on the merchant API require an `Idempotency-Key` header (1 to 255 characters):
`POST /payment_intents`, `/authorize`, `/capture`, `/refunds`. `POST /customers` does not use one.

- The key is hashed with `IDEMPOTENCY_KEY_PEPPER`. The request fingerprint covers API version,
  method, route template, path parameters, and canonical body. The same key with a different
  fingerprint returns `409 IDEMPOTENCY_KEY_REUSED`.
- A replay of a terminal command returns the stored status, headers, and bytes unchanged, with
  `Idempotency-Replayed: true`. A replay of a command still in flight returns the current `202`
  envelope `{operationId, resourceId, status, reviewReason}`.
- Several compatible keys on a capture converge on one provider operation; when it finalizes,
  every key replays identical bytes ([ADR-001](../adr/001-route-aware-idempotency.md)).
- There is no key expiry.
- Admin routes that take an `Idempotency-Key` declare it as a plain required header, so a missing
  key there is a `422 VALIDATION_ERROR`, not `400 IDEMPOTENCY_KEY_REQUIRED`.

## Pagination

List endpoints return `{"data": [...], "nextCursor": ...}`, ordered by `createdAt` then `id`
descending. Query parameters: `limit` (1 to 100, default 25), `after` (opaque cursor), and
`merchantReference` on payment intents. Cursors are HMAC-signed and bound to the filters; a
malformed, tampered, or mismatched cursor returns `400 INVALID_CURSOR`. Clients must not decode
or construct cursors. Some admin list routes cap results at 500 rows instead of paginating.

## Merchant API (`/api/v1`)

| Method and path | Scope | Idempotency-Key | Result |
| --- | --- | :---: | --- |
| `POST /customers` | `customers:write` | no | `201` `{id, merchantCustomerReference, displayName}`; duplicate reference is `409` |
| `POST /payment_intents` | `payments:write` | yes | `201` `{id, customerId, merchantReference, amount, currency, status}` |
| `GET /payment_intents` | `payments:read` | no | Page of summaries with `authorizationStatus` and `captureStatus` |
| `GET /payment_intents/{id}` | `payments:read` | no | `{id, merchantReference, amount, currency, authorizationStatus, captureStatus, capturedAmount, reservedOrRefundedAmount, refundableAmount}` |
| `POST /payment_intents/{id}/authorize` | `payments:write` | yes | `200` terminal or `202` unresolved operation envelope |
| `POST /payment_intents/{id}/capture` | `payments:write` | yes | `200` terminal or `202` unresolved; requires a succeeded authorization |
| `POST /payment_intents/{id}/refunds` | `payments:write` | yes | `200` terminal or `202` unresolved; amount may not exceed the capture minus existing refunds |
| `GET /operations/{operation_id}` | `payments:read` | no | Current operation envelope |

`POST /payment_intents` takes `customer_id` (`cus_` plus 32 hex), `merchant_reference`, and
`amount`. Authorize and capture take an empty JSON object; capture is always for the full
authorized amount. Public identifiers are prefixed (`cus_`, `pay_`, `op_`, `cap_`, `rfd_`,
`evt_`, `jrn_`).

Command endpoints dispatch to the provider after the transaction commits. They return `200`
with the stored terminal response when synchronous finalization succeeds, or `202` while the
outcome remains unresolved; read the operation or payment to follow recovery.
Operation status is one of `PROCESSING`, `SUCCEEDED`, `FAILED`, `REQUIRES_REVIEW`.

## Session API

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /api/session/login` | none (rate limited) | Create a session |
| `GET /api/session/me` | session | Session identity, memberships, `csrfToken` (read-only) |
| `POST /api/session/logout` | session + CSRF | Revoke the session |
| `GET /api/v1/payment_intents/{id}/evidence` | session | Redacted evidence bundle: resources, idempotency records, provider operations and attempts, operation history, ledger, events, recipients, deliveries and delivery attempts. Capped at 100 rows per collection |
| `POST /api/v1/operations/{id}/retry_lookup` | session + CSRF + org admin | One more status lookup for an operation in `REQUIRES_REVIEW`; never resends the mutation |
| `GET /api/v1/webhook_deliveries/{id}` | session | Delivery with its event, endpoint version, and up to 100 attempts |
| `POST /api/v1/webhook_deliveries/{id}/replay` | session + CSRF + org admin | New delivery from a `DELIVERED` or `DEAD_LETTER` one |
| `POST /api/demo/scenarios`, `GET /api/demo/scenarios/{id}` | session (+ CSRF, org admin for POST) | Run and read the lost-response scenario; `201` on create |

## Admin API (`/api/admin/v1`)

Session authentication; writes require CSRF. `{E}` is
`/api/admin/v1/environments/{environment_id}`. Roles are checked in the handlers and the service
layer (see `apps/api/routes/admin.py`); the table lists the permission where the handler states it.

| Area | Routes under `{E}` unless noted | Notes |
| --- | --- | --- |
| Environments, tenancy | `GET /api/admin/v1/environments`; `POST /api/admin/v1/organisations` (platform admin); `GET`/`PUT /api/admin/v1/memberships` | `PUT` needs `ORGANISATION_ADMIN` |
| API keys | `POST /api-keys`, `POST /api-keys/{id}/rotate`, `/versions/{n}/activate`, `/revoke`, `PATCH /api-keys/{id}/scopes` | `ORGANISATION_ADMIN`; audited |
| Generic lists | `GET /operations/{resource}` | Cursor-paginated lists over an allowlist of resources |
| Payment investigation | `GET /operations/payments?status=captured` | Optional persisted status filter; cursor binds to the filter; links to payment evidence in the console |
| Ledger investigation | `GET /operations/journals`, `GET /journals/{id}` | Journal list and read-only postings detail with `debitTotal`, `creditTotal`, and `balanced` |
| Fee schedules | `GET /operations/fee-schedules` | Read-only fee schedule versions per merchant account (`version`, `effectiveFrom`, `mdrBps`, `fixedPaise`, `gstBps`, bounds, `refundFeePolicy`); the current terms are the highest version whose `effectiveFrom` has passed |
| Merchant accounts and balances | `GET`/`POST /merchant-accounts`, `GET /merchant-accounts/{id}/balances`, `/balance-transactions`, `POST /merchant-accounts/{id}/settlements` | Settlement takes an `Idempotency-Key` |
| Payouts | `GET`/`POST /beneficiaries`, `GET`/`POST /payouts`, `POST /payouts/{id}/attempts` | Create and retry take an `Idempotency-Key` |
| Reconciliation | `POST /statement-imports` (multipart: `provider`, `sourceReference`, `sourceFormat` CSV or JSON, `file`), `GET /reconciliation-mismatches`, `POST .../{id}/acknowledge`, `/resolve`, `/evidence-versions` | Statement file is capped at 1 MiB; the whole request is capped at 2 MiB (`REQUEST_BODY_TOO_LARGE`, see [error catalog](error-catalog.md#request-size)) |
| Connectors | `POST /connector-versions`, `.../{id}/verify`, `.../{id}/activate` | Verify and activate return `204` |
| Disputes | `GET /disputes`, `GET /disputes/{id}`, `POST .../agent-drafts`, `.../drafts`, `.../packages`, `GET /dispute-packages/{id}/download`, `POST .../submit` | Submit needs `workflows:write` and a session user |
| Subscription recovery | `GET`/`POST /subscriptions`, `POST /subscriptions/{id}/invoices`, `POST /subscription-invoices/{id}/failures`, `GET /recovery-cases[/{id}]`, `POST /recovery-actions/{id}/execute`, `POST /recovery-cases/{id}/opt-outs` | Reads need `financial:read`; writes `workflows:write` |
| Settlement intelligence | `GET`/`POST /settlement-policies`, `POST /merchant-accounts/{id}/settlement-forecasts`, `GET /settlement-forecasts[/{id}]`, `GET`/`POST /settlement-questions`, `GET /settlement-questions/{id}` | Questions take an `Idempotency-Key` |
| Risk review | `GET`/`POST /risk-reviews`, `GET /risk-reviews/{id}`, `POST .../annotations`, `POST .../escalation/disposition` | Disposition needs `approvals:write` |
| Workflows and approvals | `GET /workflow-runs[/{id}]`, `POST /approval-requests/{id}/decisions` | Decisions need `approvals:write` and a different user than the maker |
| Analytics | `GET /analytics/portfolio` | `technical:read` |

The payment status filter accepts `created`, `requires_action`, `authorised`, `captured`,
`partially_refunded`, `refunded`, `failed`, `expired`, and `cancelled`. An unknown status or
a status filter on another resource returns `422 VALIDATION_ERROR`. A missing or foreign
environment or journal returns `404 RESOURCE_NOT_FOUND`. Payment and journal investigation
reads use the same session and tenant/environment scope as the existing generic lists.

## Inbound connector events

`POST /api/inbound/v1/connectors/{connector_id}/events` accepts provider-originated events for a
connector. Headers: `X-Provider-Event-ID`, `X-Provider-Timestamp` (Unix seconds), and
`X-Provider-Signature`, the hex HMAC-SHA256 of `"<timestamp>." + raw body` (an optional `v1=`
prefix is accepted) keyed with `COMMERCE_CONTROL_SECRET`. Timestamps outside
`INBOUND_WEBHOOK_REPLAY_SECONDS` (default 300) are rejected. The body cap runs first; the signature
is checked before the body is parsed. Accepted bytes are stored once and processed by the poller
([backlog](../operations/runbook.md#inbound-connector-event-backlog)). Response `202`; a
byte-identical replay returns `200` with `Inbound-Replayed: true`; the same provider event id with
different bytes returns `409 WEBHOOK_EVENT_CONFLICT`. When `EDGE_ORIGIN_SIGNATURE_REQUIRED=true`
this path (like `/api/v1*`) also requires the edge origin signature.

## Inbound PSP webhooks

`POST /api/inbound/v1/psp/{provider}` receives signed webhooks from a payment provider. The path
segment is upper-cased, and only `SIMULATOR` (the provider simulator) has an adapter wired. Any
other value returns `404 PSP_PROVIDER_UNSUPPORTED` before the body is parsed. The Razorpay and
Cashfree adapters exist as code only and are not routed; see [limitations](../limitations.md).
When `EDGE_ORIGIN_SIGNATURE_REQUIRED=true`, this path also requires the edge origin signature.

| Header | Value |
| --- | --- |
| `X-Provider-Event-Id` | Provider event id; must equal the body's `eventId` |
| `X-Provider-Timestamp` | Unix seconds |
| `X-Provider-Signature` | `v1=` (the prefix is optional) plus the hex HMAC-SHA256 of `"<timestamp>." + raw body`, keyed with `PROVIDER_WEBHOOK_SECRET` |

The body is a simulator envelope, `{eventId, eventType: "effect.updated", occurredAt, effect}`, where
`effect.stableKey` names the operation. Unknown top-level fields are rejected.

The checks run in this order, after the API body cap. The body is not parsed until the signature passes.

1. All three headers present (`401 PSP_WEBHOOK_HEADERS_MISSING`).
2. Timestamp is an integer (`401 INVALID_WEBHOOK_TIMESTAMP`).
3. Replay window: `|now - timestamp|` is at most `PSP_WEBHOOK_REPLAY_SECONDS` (default 300, minimum
   30). The window is symmetric, so a timestamp in the future is refused too
   (`401 WEBHOOK_REPLAY_WINDOW_EXCEEDED`).
4. Signature, compared in constant time (`401 INVALID_WEBHOOK_SIGNATURE`).
5. Envelope valid (`400 INVALID_PSP_EVENT`), and body `eventId` equal to the header
   (`400 PSP_EVENT_ID_MISMATCH`).
6. Dedupe on `(provider, provider_event_id)`. The same bytes are a replay. Different bytes are a
   conflict (`409 PSP_EVENT_CONFLICT`).

The replay window also applies to redeliveries. A byte-identical redelivery whose timestamp is
outside the window is refused with `401`, even though its event is already stored.

Response `200 {"id", "replayed", "status"}`. `id` is the `pev_` public id. `replayed` is `false` for a
new event, and `true` for a redelivery, in which case `status` is the stored row's status. A new
event's `status` is `PENDING` when its stable key matches exactly one provider operation, and
`UNMATCHED` otherwise. Acceptance changes no operation. The poller applies stored events every second
(`apply_psp_events`). See the [runbook](../operations/runbook.md#psp-inbox) for each status.

## Merchant webhooks (RelayPay to the receiver)

Events are created only when an operation finalizes successfully: `payment.authorized.v1`,
`payment.captured.v1`, `refund.succeeded.v1`. The body is canonical JSON:
`{id, type, schemaVersion: 1, occurredAt, data: {amount, currency, operationId, paymentIntentId,
resourceId}}`.

Delivery is a `POST` to `{RECEIVER_BASE_URL}/webhooks/relaypay` (the only URL the transport will
call; redirects are not followed) with:

| Header | Value |
| --- | --- |
| `X-RelayPay-Event-Id` | `evt_...` |
| `X-RelayPay-Timestamp` | Unix seconds |
| `X-RelayPay-Signature` | `v1=` plus hex HMAC-SHA256 of `"<timestamp>." + exact event bytes`, keyed with the endpoint version's secret |

Endpoint secrets are stored AES-256-GCM encrypted. A `2xx` acknowledges; `408`, `429`, `5xx`, and
transport errors retry; any other status, including a `3xx` (redirects are not followed), is
permanent. A delivery makes at most 8 attempts in total, then goes to `DEAD_LETTER`. An unreadable
secret dead-letters immediately.

Retry *n* (after *n* failed attempts) waits a uniformly random delay in
`[0, min(3600, 5 * 2^(n-1))]` seconds (full jitter, reproducible per delivery and attempt). With 8
attempts the ceilings are 5, 10, 20, 40, 80, 160 and 320 seconds, so the 3600-second cap is never
reached. The worst-case total across the schedule is 635 seconds (about 10.6 minutes). The poller
claims due deliveries every second, up to 50 per tick, under a 30-second lease. The transport times
out after 3 seconds.

The transport calls only `{RECEIVER_BASE_URL}/webhooks/relaypay`. Any other destination is refused
before sending. The refusal is recorded as `WEBHOOK_TRANSPORT_ERROR` and retried like a transport
error.

The receiver rejects a bad or missing timestamp, a timestamp more than 300 seconds from its clock, a
bad signature, invalid JSON, and a body `id` that differs from the header, all with
`401 INVALID_WEBHOOK`. It returns `200 {received, eventId, duplicate}`, with `duplicate: true` for a
repeat of the same bytes, and `409 EVENT_CONTRADICTION` for the same event id with a different
digest. Verification code: `packages/relaypay_sdk/webhooks.py`.

## Simulator endpoints

These are internal to the compose network. Business endpoints are unauthenticated; `/control/*`
requires the matching `X-*-Control` secret header.

| Service | Endpoints |
| --- | --- |
| provider `:8001` | `POST /v1/effects`, `GET /v1/effects/{stable_key}?account_id=`, control: `POST /control/faults`, `POST /control/chaos`, `GET /control/effects/{stable_key}/proof`, `POST /control/statements`. Faults: `LOSE_RESPONSE`, `DECLINE`, `MALFORMED`, `UNSIGNED`, `MISMATCHED`, `PENDING`, `SLOW_RESPONSE`. A chaos profile (basis points per aspect, plus `seed`) draws each decision from `sha256(seed\|stable key\|aspect)`; one-shot faults win. With `PROVIDER_WEBHOOK_URL` set, every effect pushes one signed `effect.updated` webhook; see [provider chaos and push](#provider-chaos-and-push) |
| bank `:8003` | `POST /v1/transfers`, `GET /v1/transfers/{stable_key}`, control: `POST /control/faults` (`LOSE_RESPONSE`, `DECLINE`, `PENDING`) |
| commerce `:8004` | control-secret protected: `POST /v1/orders`, `/v1/orders/{id}/payment-links`, `/v1/events/{id}` |
| dispute network `:8005` | `POST /v1/submissions`, `GET /v1/submissions/{stable_key}` |
| recovery network `:8006` | `POST /v1/messages`, `POST /v1/payment-retries`, and `GET` lookups by stable key |
| merchant site `:8007` | `GET /v1/sites/{site_ref}/snapshot` |
| receiver `:8002` | `POST /webhooks/relaypay`, `GET /health/ready` |

### Provider chaos and push

`POST /control/chaos` (header `X-Provider-Control`, the `PROVIDER_CONTROL_SECRET`) installs a seeded
profile for one account and returns `204`. A wrong or missing secret returns `401 UNAUTHENTICATED`.
An unknown field or an out-of-range value returns `422 VALIDATION_ERROR`. Fields, in camelCase:

| Field | Range | Effect |
| --- | --- | --- |
| `accountId` | required | Provider account public id |
| `enabled` | boolean, default `true` | `false` removes the account's profile |
| `seed` | 0 to 2^62, default 0 | Seed for every draw |
| `errorBeforeBps` | 0 to 10000 | `503` before anything is committed; no effect is created |
| `errorAfterBps` | 0 to 10000 | Commits the effect, then `503` |
| `loseResponseBps` | 0 to 10000 | Commits the effect, then replies `599` with an empty body |
| `slowBps`, `slowMs` | 0 to 10000 bps; 0 to 10000 ms | When hit, sleeps `slowMs` after the commit |
| `pendingBps`, `declineBps` | 0 to 10000 | Outcome `PENDING` (takes precedence) or `DECLINED`; otherwise `SUCCEEDED` |
| `webhookDropBps`, `webhookDuplicateBps` | 0 to 10000 | The event is kept but never sent, or is sent twice |
| `webhookMaxDelayMs` | 0 to 600000 | First send delayed by `draw * webhookMaxDelayMs / 10000` ms |

Each decision is `draw(seed, stableKey, aspect)`: the first 8 bytes of
`sha256("<seed>|<stableKey>|<aspect>")`, modulo 10000. It is a hit when the draw is below the
basis-point value. Decisions apply to the first mutation of a stable key. A one-shot fault from
`/control/faults` takes precedence over the profile.

With `PROVIDER_WEBHOOK_URL` set, each effect enqueues one `effect.updated` event in the same
transaction as the effect. The pusher sends due events to that URL every `PROVIDER_WEBHOOK_PUSH_SECONDS`
(default 0.5), up to 50 per batch, with a 5-second HTTP timeout. Each request carries
`X-Provider-Event-Id`, `X-Provider-Timestamp` (Unix seconds), and `X-Provider-Signature`: `v1=` plus
the HMAC-SHA256 of `"<timestamp>." + body`, keyed with `PROVIDER_WEBHOOK_SECRET`.

Delivery is at least once. A failed send (non-2xx or transport error) waits `min(2^n, 60)` seconds
after the *n*th failure. The event becomes `DEAD` after 8 failed sends, which is 182 seconds of
cumulative waiting. The push is best effort, and polling remains the guarantee.

The dispute, recovery, and merchant-site simulators use FastAPI's default `{"detail": ...}` error
shape rather than the RelayPay envelope.

## /api/v2 (v2.0)

v2 adds the payment commands whose behaviour v2.0 changed. Creating payments, refunds, listing and
operation reads are unchanged and stay on `/api/v1`. Every v2 command takes an `Idempotency-Key`.
v2 fingerprints are versioned, so a key already used on a v1 route returns
`409 IDEMPOTENCY_KEY_REUSED` on v2 and never replays across versions.

The capture response exposes fee/GST fields, and the schema and finalizer support them.
The fee calculator is still a maintainer-owned stub: a merchant with a configured fee schedule
cannot complete that computation yet. The default no-schedule path has zero fees. See
[PROVENANCE.md](../../PROVENANCE.md) and [limitations](../limitations.md).

| Method and path | Body | Notes |
| --- | --- | --- |
| `GET /api/v2/payment_intents/{id}` | | Persisted `status` and `statusVersion`, `authorization` (`holdSeconds`, `expiresAt`), `capture` (`amount`, `feeAmount`, `gstAmount`, `netAmount`), `void`, and the v1 refund fields |
| `POST /api/v2/payment_intents/{id}/authorize` | `{"hold_seconds"?: 60..2592000}` | Hold defaults to `AUTH_HOLD_SECONDS` (7 days); an uncaptured payment expires afterwards |
| `POST /api/v2/payment_intents/{id}/capture` | `{"amount"?: paise}` | One capture per payment, up to the authorized amount; the remainder is released. `409 VOID_IN_PROGRESS`, `409 AUTHORIZATION_EXPIRED`, `409 PAYMENT_NOT_CAPTURABLE`, `409 CAPTURE_ALREADY_EXISTS`, `422 CAPTURE_AMOUNT_EXCEEDS_AUTHORIZED` |
| `POST /api/v2/payment_intents/{id}/void` | `{}` | Releases an authorised, uncaptured hold; the payment becomes `cancelled`. `409 VOID_REQUIRES_SUCCEEDED_AUTHORIZATION`, `409 PAYMENT_NOT_VOIDABLE` |

Payment `status` is one of `created`, `requires_action`, `authorised`, `captured`,
`partially_refunded`, `refunded`, `failed`, `expired` or `cancelled`. Transitions are enforced in
code and by a database trigger.
