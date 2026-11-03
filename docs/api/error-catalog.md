# RelayPay API error catalog

Errors from `apps/api` use `{"error": {"code", "message", "details"}}` (`details` is `null` when
empty). Treat `code` as stable and `message` as human-readable. The `X-Request-ID` response header
is safe to record for support. The Python SDK raises `RelayPayAPIError` with that request id and
reports code `UNEXPECTED_RESPONSE` for a body that does not match the envelope.

The simulator services are internal and use other shapes: the provider and bank omit `details`,
the receiver uses `INVALID_WEBHOOK` (401) and `EVENT_CONTRADICTION` (409), and the dispute,
recovery, and merchant-site simulators return FastAPI's `{"detail": ...}`.

Mutation retries are safe only when the caller reuses the original `Idempotency-Key`. RelayPay
stores terminal response bytes and returns them unchanged on a valid replay.

## Request size

Some services cap the raw request body while it is read. A declared `Content-Length` over the cap is
refused before any body byte is read. Otherwise the bytes that arrive are counted, and reading stops
at the chunk that crosses the cap, so a missing, wrong, or chunked `Content-Length` cannot bypass
the cap. A cap bounds one request; it does not limit request rate, concurrency, or total memory.

| Service | Cap | Notes |
| --- | ---: | --- |
| `apps/api`, `apps/receiver`, console backend proxy | 2 MiB | Statement files stay capped at 1 MiB, and the multipart overhead fits in the request cap |
| `apps/dispute_network` | 20 MiB | Packages are raw zips bounded by `MAX_PACKAGE_BYTES`, so a smaller cap would reject valid submissions |
| Edge Worker, `/api/v1` and webhook ingress | 1 MiB | Cap unchanged. The body is read with a bounded reader, so an oversize body is no longer buffered in full before the check |

The provider, bank, commerce, recovery-network, and merchant-site services have no body cap in this
checkout.

| Code | HTTP | Meaning | Retry guidance |
| --- | ---: | --- | --- |
| `REQUEST_BODY_TOO_LARGE` | 413 | Body over the service cap, refused before the route runs. `details.maxBytes` is the cap. The API, receiver, and dispute network return this envelope; the receiver and dispute network otherwise use their own error shapes | Send a smaller body |
| `EDGE_BODY_TOO_LARGE` | 413 | Body over the edge cap | Send a smaller body |
| `REQUEST_BODY_INCOMPLETE` | 400 | The connection closed before the body finished. Nothing was processed | Resend with the same `Idempotency-Key` |

Error bodies never echo request bytes. The 413 is a service-wide response, so it is not listed in
each route's OpenAPI responses.

## Request and authentication

| Code | HTTP | Meaning | Retry guidance |
| --- | ---: | --- | --- |
| `UNAUTHENTICATED` | 401 | Missing, invalid, expired, or revoked API key or session | Obtain a valid credential |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password at login | Do not retry unchanged |
| `ORGANISATION_CONTEXT_REQUIRED` | 409 | The user has several organisations and no `organisationId` was sent | Resend with `organisationId` |
| `FORBIDDEN` | 403 | Valid credential without the required scope or role | Request the permission |
| `ADMIN_SESSION_REQUIRED` | 403 | An admin route was called with an API key | Use a session |
| `CSRF_INVALID` | 403 | Missing or wrong `X-CSRF-Token` on a session write | Re-read `/api/session/me` |
| `RESOURCE_NOT_FOUND` | 404 | Absent, or outside the caller's organisation or environment | Do not retry unchanged |
| `NOT_FOUND` | 404 | Returned by the evidence route and by `/metrics` when disabled | Do not retry unchanged |
| `VALIDATION_ERROR` | 422 | Request failed strict schema validation; `details.errors` lists the fields | Correct the request |
| `INVALID_CURSOR` | 400 | Cursor is malformed, tampered, or bound to different filters | Restart pagination |
| `RATE_LIMITED` | 429 | Login limiter only (10 per 60 s per client address, per process) | Honor `Retry-After` |
| `DEPENDENCY_UNAVAILABLE` | 503 | Returned only by `GET /health/ready` when the database check fails; carries `Retry-After: 5` | Poll again |
| `EDGE_ORIGIN_SIGNATURE_INVALID` | 401 | Edge signature missing or invalid while `EDGE_ORIGIN_SIGNATURE_REQUIRED=true` | Send through the edge Worker |

Merchant API keys are not rate limited by the API itself.

## Idempotency and commands

| Code | HTTP | Meaning | Retry guidance |
| --- | ---: | --- | --- |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | A merchant financial command omitted its key | Retry with a new key |
| `IDEMPOTENCY_KEY_INVALID` | 400 | Key longer than 255 characters | Use a shorter key |
| `IDEMPOTENCY_KEY_REUSED` | 409 | The key is bound to a different canonical request | Use a new key |
| `COMMAND_CONFLICT` | 409 | A concurrent command won the race | Re-read the resource, then retry with the same key |
| `MERCHANT_REFERENCE_CONFLICT` | 409 | Duplicate payment `merchant_reference` | Use a unique reference |
| `MERCHANT_CUSTOMER_REFERENCE_CONFLICT` | 409 | Duplicate `merchant_customer_reference` | Use a unique reference |
| `CAPTURE_REQUIRES_SUCCEEDED_AUTHORIZATION` | 409 | Capture before a succeeded authorization | Wait for the authorization |
| `REFUND_REQUIRES_SUCCEEDED_CAPTURE` | 409 | Refund before a succeeded capture | Wait for the capture |
| `REFUND_AMOUNT_EXCEEDS_AVAILABLE` | 409 | Refund larger than capture minus existing refunds | Lower the amount |

On admin routes that take an `Idempotency-Key`, a missing key is `VALIDATION_ERROR` (422).

## Operations, deliveries, and inbound events

| Code | HTTP | Meaning |
| --- | ---: | --- |
| `LOOKUP_IN_PROGRESS` | 409 | A status lookup for the operation is already in progress |
| `LOOKUP_NOT_AVAILABLE` | 409 | Lookup requested before the first send was recorded |
| `OPERATION_NOT_IN_REVIEW` | 409 | `retry_lookup` is available only for operations in `REQUIRES_REVIEW` |
| `OPERATION_TERMINAL` | 409 | A terminal operation needs no lookup |
| `RESOURCE_NOT_SUPPORTED` | 404 | Resource name not on the generic admin list allowlist |
| `DELIVERY_NOT_TERMINAL` | 409 | Replay of a delivery that is not `DELIVERED` or `DEAD_LETTER` |
| `INVALID_WEBHOOK_TIMESTAMP`, `WEBHOOK_REPLAY_WINDOW_EXCEEDED`, `INVALID_WEBHOOK_SIGNATURE` | 401 | Inbound event failed authentication |
| `WEBHOOK_EVENT_CONFLICT` | 409 | Same provider event id with different bytes |
| `EVENT_CONTAINS_PII`, `EVENT_DIGEST_MISMATCH`, `EVENT_EVIDENCE_TOO_DEEP`, `UNSUPPORTED_EVENT` | 422 | Business-event intake (dispute and recurring-payment failure) rejected the evidence |
| `EVENT_REPLAY_CONFLICT` | 409 | Event ID is already bound to a different organisation, environment, or payload digest. A replay whose event has no matching dispute case or recurring attempt in the caller's scope also returns this |
| `EVENT_TOO_LARGE` | 413 | Evidence over the size bound |
| `INVALID_KEY_VERSION` | 409 | Pending API key version not found at activation |

## Domain codes

Admin workflows return these. All are `409` unless noted.

| Area | Codes |
| --- | --- |
| Balances and payouts | `INSUFFICIENT_PAYOUT_BALANCE`, `PAYOUT_IN_PROGRESS`, `PAYOUT_RETRY_NOT_ALLOWED`, `SETTLEMENT_IN_PROGRESS`, `DEFAULT_MERCHANT_ACCOUNT_EXISTS` |
| Reconciliation | `STATEMENT_SOURCE_CONFLICT`, `INVALID_MISMATCH_TRANSITION`; 422: `INVALID_STATEMENT`, `INVALID_STATEMENT_PERIOD`, `INVALID_WORKFLOW_NOTE`, `INVALID_WORKFLOW_STATUS` |
| Connectors | `CONNECTOR_REFERENCE_CONFLICT`, `CONNECTOR_VERIFICATION_FAILED`, `CONNECTOR_VERSION_NOT_PENDING`, `CONNECTOR_VERSION_NOT_VERIFIED` |
| Approvals and workflow steps | `LEASE_LOST` (workflow step lease no longer valid); 403: `MAKER_CHECKER_REQUIRED`, `SESSION_USER_REQUIRED` |
| Disputes | `PACKAGE_NOT_APPROVED`, `PACKAGE_INVALIDATED_BEFORE_SUBMISSION`; 422: `INVALID_CITATION` |
| Subscription recovery | `AMBIGUOUS_RECURRING_PAYMENT`, `COMMUNICATION_EVIDENCE_MISSING`, `INVOICE_SOURCE_CONFLICT`, `RECOVERY_ACTION_NOT_DUE`, `RECOVERY_ACTION_TYPE_MISMATCH`, `RECOVERY_CASE_TERMINAL`, `RECOVERY_NOT_REQUIRED`, `RECOVERY_POLICY_EXPIRED`, `RECURRING_ATTEMPT_CONFLICT`, `SUBSCRIPTION_SOURCE_CONFLICT` |
| Settlement questions | `SETTLEMENT_QUESTION_CONCURRENT`, `SETTLEMENT_QUESTION_KEY_REUSED`; 422: `SETTLEMENT_POLICY_INVALID`, `SETTLEMENT_QUESTION_INVALID`, and the `SETTLEMENT_NARRATIVE_*` validation codes (`CITATION_UNKNOWN`, `CROSS_RECORD_REFERENCE`, `MISSING_CITATION`, `NUMBER_MISMATCH`, `UNSUPPORTED_CLAIM`); 502: `SETTLEMENT_CLASSIFICATION_INVALID`, `SETTLEMENT_NARRATIVE_INVALID`; 500: `SETTLEMENT_NARRATIVE_MISSING` |
| Risk review | `RISK_ESCALATION_CLOSED`; 422: `RISK_ANNOTATION_INVALID`; 502: `RISK_FINDINGS_INVALID`; 503: `SITE_SNAPSHOT_UNAVAILABLE`; 404: `SITE_SNAPSHOT_UNKNOWN` |
| Internal | 500: `LEDGER_ACCOUNT_MISSING` (a required ledger account was not seeded) |

`RECURRING_ATTEMPT_CONFLICT` means a provider attempt ID is already recorded for a different
invoice, organisation, environment, provider code, outcome, or evidence digest.

The simulators add `PROVIDER_ACCOUNT_NOT_FOUND`, `PROVIDER_EFFECT_NOT_FOUND`,
`PROVIDER_EFFECT_CONFLICT`, `PROVIDER_KEY_CONFLICT`, `BANK_ACCOUNT_NOT_FOUND`,
`BANK_EFFECT_NOT_FOUND`, `BANK_KEY_CONFLICT`, and `BENEFICIARY_REJECTED` on their own endpoints.

Provider-attempt classifications such as `AMBIGUOUS_HTTP_5XX`, `AMBIGUOUS_TRANSPORT`,
`INVALID_SIGNATURE`, `INVALID_SCHEMA`, and `MISMATCHED_EVIDENCE` are stored on attempts and shown
in payment evidence; they are not HTTP error codes.

Response headers worth knowing: `Idempotency-Replayed: true` on a stored replay,
`Inbound-Replayed: true` on a repeated inbound event, and `Retry-After` on `RATE_LIMITED` and
`DEPENDENCY_UNAVAILABLE`. The SDK's `retry_guidance()` treats only 429, 502, 503, and 504 as
transient; it does not classify `COMMAND_CONFLICT` as retryable.
