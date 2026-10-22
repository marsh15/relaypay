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
[error catalog](error-catalog.md).

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
| `POST /payment_intents/{id}/authorize` | `payments:write` | yes | `202` operation envelope |
| `POST /payment_intents/{id}/capture` | `payments:write` | yes | `202` operation envelope; requires a succeeded authorization |
| `POST /payment_intents/{id}/refunds` | `payments:write` | yes | `202`; amount may not exceed the capture minus existing refunds |
| `GET /operations/{operation_id}` | `payments:read` | no | Current operation envelope |

`POST /payment_intents` takes `customer_id` (`cus_` plus 32 hex), `merchant_reference`, and
`amount`. Authorize and capture take an empty JSON object; capture is always for the full
authorized amount. Public identifiers are prefixed (`cus_`, `pay_`, `op_`, `cap_`, `rfd_`,
`evt_`, `jrn_`).

Command endpoints dispatch to the provider after the transaction commits and return `202` even
when the provider answered synchronously; read the operation or payment to see the terminal state.
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
| Merchant accounts and balances | `GET`/`POST /merchant-accounts`, `GET /merchant-accounts/{id}/balances`, `/balance-transactions`, `POST /merchant-accounts/{id}/settlements` | Settlement takes an `Idempotency-Key` |
| Payouts | `GET`/`POST /beneficiaries`, `GET`/`POST /payouts`, `POST /payouts/{id}/attempts` | Create and retry take an `Idempotency-Key` |
| Reconciliation | `POST /statement-imports` (multipart: `provider`, `sourceReference`, `sourceFormat` CSV or JSON, `file`), `GET /reconciliation-mismatches`, `POST .../{id}/acknowledge`, `/resolve`, `/evidence-versions` | Statement size is capped at 1 MiB |
| Connectors | `POST /connector-versions`, `.../{id}/verify`, `.../{id}/activate` | Verify and activate return `204` |
| Disputes | `GET /disputes`, `GET /disputes/{id}`, `POST .../agent-drafts`, `.../drafts`, `.../packages`, `GET /dispute-packages/{id}/download`, `POST .../submit` | Submit needs `workflows:write` and a session user |
| Subscription recovery | `GET`/`POST /subscriptions`, `POST /subscriptions/{id}/invoices`, `POST /subscription-invoices/{id}/failures`, `GET /recovery-cases[/{id}]`, `POST /recovery-actions/{id}/execute`, `POST /recovery-cases/{id}/opt-outs` | Reads need `financial:read`; writes `workflows:write` |
| Settlement intelligence | `GET`/`POST /settlement-policies`, `POST /merchant-accounts/{id}/settlement-forecasts`, `GET /settlement-forecasts[/{id}]`, `GET`/`POST /settlement-questions`, `GET /settlement-questions/{id}` | Questions take an `Idempotency-Key` |
| Risk review | `GET`/`POST /risk-reviews`, `GET /risk-reviews/{id}`, `POST .../annotations`, `POST .../escalation/disposition` | Disposition needs `approvals:write` |
| Workflows and approvals | `GET /workflow-runs[/{id}]`, `POST /approval-requests/{id}/decisions` | Decisions need `approvals:write` and a different user than the maker |
| Analytics | `GET /analytics/portfolio` | `technical:read` |

## Inbound connector events

`POST /api/inbound/v1/connectors/{connector_id}/events` accepts provider-originated events for a
connector. Headers: `X-Provider-Event-ID`, `X-Provider-Timestamp` (Unix seconds), and
`X-Provider-Signature`, the hex HMAC-SHA256 of `"<timestamp>." + raw body` (an optional `v1=`
prefix is accepted) keyed with `COMMERCE_CONTROL_SECRET`. Timestamps outside
`INBOUND_WEBHOOK_REPLAY_SECONDS` (default 300) are rejected. The signature is checked before the
body is parsed; accepted bytes are stored once and processed by the poller. Response `202`; a
byte-identical replay returns `200` with `Inbound-Replayed: true`; the same provider event id with
different bytes returns `409 WEBHOOK_EVENT_CONFLICT`. When `EDGE_ORIGIN_SIGNATURE_REQUIRED=true`
this path (like `/api/v1*`) also requires the edge origin signature.

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
transport errors retry; any other status is permanent. At most 8 attempts, then `DEAD_LETTER`.
Retry *n* waits a random delay in `[0, min(3600, 5 * 2^(n-1))]` seconds (full jitter, reproducible
per delivery), which rides out about 10 minutes of receiver failure in the worst case. An unreadable secret dead-letters immediately. The
receiver rejects timestamps more than 300 seconds old, requires the body `id` to match the header,
returns `200 {duplicate: true}` for a repeat of the same bytes, and `409 EVENT_CONTRADICTION` for
the same event id with a different digest. Verification code: `packages/relaypay_sdk/webhooks.py`.

## Simulator endpoints

These are internal to the compose network. Business endpoints are unauthenticated; `/control/*`
requires the matching `X-*-Control` secret header.

| Service | Endpoints |
| --- | --- |
| provider `:8001` | `POST /v1/effects`, `GET /v1/effects/{stable_key}?account_id=`, control: `POST /control/faults`, `POST /control/chaos`, `GET /control/effects/{stable_key}/proof`, `POST /control/statements`. Faults: `LOSE_RESPONSE`, `DECLINE`, `MALFORMED`, `UNSIGNED`, `MISMATCHED`, `PENDING`, `SLOW_RESPONSE`. A chaos profile (basis points per aspect, plus `seed`) draws each decision from `sha256(seed\|stable key\|aspect)`; one-shot faults win. With `PROVIDER_WEBHOOK_URL` set, every effect pushes one signed `effect.updated` webhook, which the profile may drop, duplicate or delay |
| bank `:8003` | `POST /v1/transfers`, `GET /v1/transfers/{stable_key}`, control: `POST /control/faults` (`LOSE_RESPONSE`, `DECLINE`, `PENDING`) |
| commerce `:8004` | control-secret protected: `POST /v1/orders`, `/v1/orders/{id}/payment-links`, `/v1/events/{id}` |
| dispute network `:8005` | `POST /v1/submissions`, `GET /v1/submissions/{stable_key}` |
| recovery network `:8006` | `POST /v1/messages`, `POST /v1/payment-retries`, and `GET` lookups by stable key |
| merchant site `:8007` | `GET /v1/sites/{site_ref}/snapshot` |
| receiver `:8002` | `POST /webhooks/relaypay`, `GET /health/ready` |

The dispute, recovery, and merchant-site simulators use FastAPI's default `{"detail": ...}` error
shape rather than the RelayPay envelope.

## /api/v2 (v2.0)

v2 adds the payment commands whose behaviour v2.0 changed. Creating payments, refunds, listing and
operation reads are unchanged and stay on `/api/v1`. Every v2 command takes an `Idempotency-Key`.
v2 fingerprints are versioned, so a key already used on a v1 route returns
`409 IDEMPOTENCY_KEY_REUSED` on v2 and never replays across versions.

| Method and path | Body | Notes |
| --- | --- | --- |
| `GET /api/v2/payment_intents/{id}` | | Persisted `status` and `statusVersion`, `authorization` (`holdSeconds`, `expiresAt`), `capture` (`amount`, `feeAmount`, `gstAmount`, `netAmount`), `void`, and the v1 refund fields |
| `POST /api/v2/payment_intents/{id}/authorize` | `{"hold_seconds"?: 60..2592000}` | Hold defaults to `AUTH_HOLD_SECONDS` (7 days); an uncaptured payment expires afterwards |
| `POST /api/v2/payment_intents/{id}/capture` | `{"amount"?: paise}` | One capture per payment, up to the authorized amount; the remainder is released. `409 VOID_IN_PROGRESS`, `409 AUTHORIZATION_EXPIRED`, `409 PAYMENT_NOT_CAPTURABLE`, `409 CAPTURE_ALREADY_EXISTS`, `422 CAPTURE_AMOUNT_EXCEEDS_AUTHORIZED` |
| `POST /api/v2/payment_intents/{id}/void` | `{}` | Releases an authorised, uncaptured hold; the payment becomes `cancelled`. `409 VOID_REQUIRES_SUCCEEDED_AUTHORIZATION`, `409 PAYMENT_NOT_VOIDABLE` |

Payment `status` is one of `created`, `requires_action`, `authorised`, `captured`,
`partially_refunded`, `refunded`, `failed`, `expired` or `cancelled`. Transitions are enforced in
code and by a database trigger.
