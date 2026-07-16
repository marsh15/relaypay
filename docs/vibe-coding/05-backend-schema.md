# 05 — Backend Schema and Access Architecture

## Conventions

- PostgreSQL is authoritative. The schema below is for the `relaypay` database unless marked provider/receiver.
- Primary keys are UUID/UUIDv7 values. Public IDs are stable prefixed text values with unique constraints.
- Every tenant-owned table carries `organisation_id` even when derivable; composite foreign keys prevent cross-tenant relationships.
- Timestamps are `timestamptz` in UTC. Mutable rows have `created_at` and `updated_at`; immutable evidence has `created_at` only.
- Money is `bigint` paise with `CHECK (amount > 0)` and fixed `currency = 'INR'`.
- Exact canonical payloads are `bytea`; safe query/debug summaries are separate `jsonb` columns.
- Enums may be PostgreSQL enums or constrained text, but migrations must make state additions explicit.
- PostgreSQL RLS is intentionally not used.

## Identity and Tenancy

### `organisations`

- `id uuid` primary key
- `public_id text` unique, not null
- `name text` not null
- `status text` not null (`ACTIVE`, `DISABLED`)
- `created_at timestamptz` not null
- `updated_at timestamptz` not null

### `users`

- `id uuid` primary key
- `organisation_id uuid` not null → `organisations.id`
- `email_normalized text` not null
- `display_name text` not null
- `password_hash text` not null
- `role text` not null (`ADMIN`)
- `status text` not null (`ACTIVE`, `DISABLED`)
- `created_at timestamptz` not null
- `updated_at timestamptz` not null
- unique `(organisation_id, id)` for composite references
- unique `(organisation_id, email_normalized)`

### `sessions`

- `id uuid` primary key
- `organisation_id uuid` not null
- `user_id uuid` not null
- `token_digest bytea` unique, not null
- `csrf_digest bytea` not null
- `expires_at timestamptz` not null
- `last_seen_at timestamptz` null
- `revoked_at timestamptz` null
- `created_at timestamptz` not null
- composite FK `(organisation_id, user_id)` → `users(organisation_id, id)`
- index `(token_digest)` for authentication
- index `(expires_at)` where `revoked_at IS NULL` for cleanup

### `api_keys`

- `id uuid` primary key
- `organisation_id uuid` not null → `organisations.id`
- `name text` not null
- `public_prefix text` unique, not null
- `secret_digest bytea` not null
- `scopes text[]` not null
- `status text` not null (`ACTIVE`, `REVOKED`)
- `last_used_at timestamptz` null
- `created_at timestamptz` not null
- `revoked_at timestamptz` null
- index `(organisation_id, status)`

Only the prefix and peppered digest are stored. The plaintext key is displayed once during seeding/setup and never logged.

## Customers and Payments

### `customers`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null → `organisations.id`
- `merchant_customer_reference text` not null
- `display_name text` null; synthetic-only
- `created_at timestamptz` not null
- unique `(organisation_id, id)`
- unique `(organisation_id, merchant_customer_reference)`

### `payment_intents`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `customer_id uuid` not null
- `merchant_reference text` not null
- `amount bigint` not null, check positive
- `currency text` not null, check `currency = 'INR'`
- `created_at timestamptz` not null
- `updated_at timestamptz` not null
- composite FK `(organisation_id, customer_id)` → `customers(organisation_id, id)`
- unique `(organisation_id, id)`
- unique `(organisation_id, merchant_reference)`
- index `(organisation_id, created_at DESC)` for bounded administrative reads

No mutable aggregate status or refundable amount authorizes commands. The evidence read model derives them from durable child state while command services inspect locked child rows.

### `authorizations`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `payment_intent_id uuid` not null
- `provider_operation_id uuid` initially deferred FK, not null before transaction commit
- `amount bigint` not null, check positive
- `currency text` not null, check INR
- `status text` not null (`PROCESSING`, `SUCCEEDED`, `FAILED`, `REQUIRES_REVIEW`)
- `failure_code text` null
- `authorized_at timestamptz` null
- `created_at timestamptz` not null
- `updated_at timestamptz` not null
- composite FK `(organisation_id, payment_intent_id)` → `payment_intents(organisation_id, id)`
- unique `(organisation_id, id)`
- unique `(organisation_id, payment_intent_id)` enforcing singleton authorization
- check terminal timestamp/failure fields match status

### `captures`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `payment_intent_id uuid` not null
- `authorization_id uuid` not null
- `provider_operation_id uuid` initially deferred FK, not null before transaction commit
- `journal_id uuid` null until verified success
- `amount bigint` not null, check positive
- `currency text` not null, check INR
- `status text` not null (`PROCESSING`, `SUCCEEDED`, `FAILED`, `REQUIRES_REVIEW`)
- `failure_code text` null
- `captured_at timestamptz` null
- `created_at timestamptz` not null
- `updated_at timestamptz` not null
- composite FKs to payment and authorization within organisation
- unique `(organisation_id, id)`
- unique `(organisation_id, payment_intent_id)` enforcing one full capture
- unique `(provider_operation_id)` and unique non-null `(journal_id)`
- constraints/triggers require `amount` to equal payment amount and successful capture to reference a posted capture journal

### `refunds`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `payment_intent_id uuid` not null
- `capture_id uuid` not null
- `provider_operation_id uuid` initially deferred FK, not null before transaction commit
- `journal_id uuid` null until verified success
- `merchant_refund_reference text` null
- `amount bigint` not null, check positive
- `currency text` not null, check INR
- `status text` not null (`PROCESSING`, `SUCCEEDED`, `FAILED`, `REQUIRES_REVIEW`)
- `failure_code text` null
- `refunded_at timestamptz` null
- `created_at timestamptz` not null
- `updated_at timestamptz` not null
- composite FKs to payment/capture within organisation
- unique `(organisation_id, id)`
- unique `(provider_operation_id)` and unique non-null `(journal_id)`
- optional unique `(organisation_id, merchant_refund_reference)` where non-null
- index `(organisation_id, payment_intent_id, status)` for locked availability calculation

The refund row is the reservation. `PROCESSING`, `REQUIRES_REVIEW`, and `SUCCEEDED` consume refundable value; verified `FAILED` does not. No separately mutable reservation counter exists.

## Idempotency and Provider Operations

### `provider_operations`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `payment_intent_id uuid` not null
- `resource_type text` not null (`AUTHORIZATION`, `CAPTURE`, `REFUND`)
- `resource_id uuid` not null
- `kind text` not null (`AUTHORIZE`, `CAPTURE`, `REFUND`)
- `stable_provider_key text` not null
- `status text` not null (`PROCESSING`, `SUCCEEDED`, `FAILED`, `REQUIRES_REVIEW`)
- `review_reason text` null (`PROVIDER_INDETERMINATE`, `APPLY_FAILURE`, `INVALID_EVIDENCE`, etc.)
- `provider_request_bytes bytea` null until send preparation
- `provider_request_sha256 bytea` null until send preparation
- `attempt_count integer` not null default 0, check non-negative
- `last_sent_at timestamptz` null
- `next_lookup_at timestamptz` null
- `lookup_lease_token uuid` null
- `lookup_lease_expires_at timestamptz` null
- `apply_failure_count integer` not null default 0
- `terminal_http_status smallint` null
- `terminal_response_headers jsonb` null; replay-safe allowlist only
- `terminal_response_bytes bytea` null
- `terminal_response_sha256 bytea` null
- `finalized_at timestamptz` null
- `created_at timestamptz` not null
- `updated_at timestamptz` not null
- composite FK `(organisation_id, payment_intent_id)` → payment
- unique `(organisation_id, id)`
- unique `(organisation_id, stable_provider_key)`
- unique `(organisation_id, kind, resource_id)`
- index `(status, next_lookup_at)` for recovery polling
- index `(lookup_lease_expires_at)` for lease reclamation
- check terminal fields are all present only for `SUCCEEDED`/`FAILED`
- check `last_sent_at IS NOT NULL` implies request bytes/digest exist and `attempt_count >= 1`

Polymorphic resource integrity is validated in the domain and with constraint triggers because a standard FK cannot target three tables. Each child has a unique FK back to its provider operation, preventing orphaned pairs at commit.

### `provider_attempts`

- `id uuid` primary key
- `organisation_id uuid` not null
- `provider_operation_id uuid` not null
- `sequence integer` not null, check positive
- `attempt_kind text` not null (`MUTATION`, `LOOKUP`)
- `state text` not null (`SENT`, `RESPONSE_RECEIVED`, `TRANSPORT_ERROR`, `VALIDATION_REJECTED`)
- `request_sha256 bytea` not null
- `response_http_status smallint` null
- `response_bytes bytea` null; sanitized or encrypted according to threat model
- `response_sha256 bytea` null
- `provider_signature_valid boolean` null
- `classification text` null
- `safe_error_code text` null
- `started_at timestamptz` not null
- `completed_at timestamptz` null
- composite FK `(organisation_id, provider_operation_id)` → operation
- unique `(provider_operation_id, sequence)`
- append-only permissions/trigger

The first mutation attempt's `SENT` row and operation send metadata commit before HTTP. Additional mutation attempts for the same operation are forbidden by a partial unique index on `provider_operation_id WHERE attempt_kind = 'MUTATION'`.

### `operation_history`

- `id uuid` primary key
- `organisation_id uuid` not null
- `provider_operation_id uuid` not null
- `from_status text` null
- `to_status text` not null
- `reason_code text` not null
- `evidence_attempt_id uuid` null
- `actor_type text` not null (`REQUEST`, `RECOVERY_WORKER`, `ADMIN_LOOKUP`, `FINALIZER`)
- `correlation_id text` not null
- `created_at timestamptz` not null
- composite FKs within organisation
- append-only permissions/trigger
- index `(provider_operation_id, created_at)`

### `idempotency_records`

- `id uuid` primary key
- `organisation_id uuid` not null → organisation
- `key_digest bytea` not null
- `key_hint text` null; non-sensitive suffix/label only
- `fingerprint_sha256 bytea` not null
- `fingerprint_summary jsonb` not null; canonical safe structure
- `target_type text` not null (`PAYMENT_INTENT`, `PROVIDER_OPERATION`)
- `target_id uuid` not null
- `provider_operation_id uuid` null
- `is_terminal boolean` not null
- `http_status smallint` null until operation terminal; always set for payment creation
- `response_headers jsonb` null; replay-safe allowlist
- `response_bytes bytea` null until terminal
- `response_sha256 bytea` null until terminal
- `created_at timestamptz` not null
- `finalized_at timestamptz` null
- unique `(organisation_id, key_digest)`
- composite FK `(organisation_id, provider_operation_id)` when non-null
- index `(provider_operation_id, id)` for ordered finalizer locking
- checks: nonterminal rows require `provider_operation_id` and target; terminal rows require status/bytes/digest; payment-intent targets are terminal at commit

Keys themselves are never stored or logged. Reuse lookup hashes the presented key with a server-side pepper before querying.

## Ledger

### `ledger_accounts`

- `id uuid` primary key
- `organisation_id uuid` not null
- `code text` not null (`PROVIDER_CLEARING_ASSET`, `MERCHANT_PAYABLE_LIABILITY`)
- `name text` not null
- `account_type text` not null (`ASSET`, `LIABILITY`)
- `currency text` not null, check INR
- `created_at timestamptz` not null
- unique `(organisation_id, id)`
- unique `(organisation_id, code, currency)`

### `journals`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `provider_operation_id uuid` not null
- `journal_type text` not null (`CAPTURE`, `REFUND`, `COMPENSATION`)
- `reference_type text` not null
- `reference_id uuid` not null
- `currency text` not null, check INR
- `posted_at timestamptz` not null
- `created_at timestamptz` not null
- unique `(organisation_id, id)`
- unique `(provider_operation_id)` for original financial transition
- unique `(organisation_id, journal_type, reference_id)` where type is capture/refund
- append-only/immutable trigger after insert

### `postings`

- `id uuid` primary key
- `organisation_id uuid` not null
- `journal_id uuid` not null
- `account_id uuid` not null
- `side text` not null (`DEBIT`, `CREDIT`)
- `amount bigint` not null, check positive
- `currency text` not null, check INR
- `created_at timestamptz` not null
- composite FKs to journal/account within organisation
- index `(organisation_id, account_id, created_at, id)` for derived balances
- append-only/immutable trigger

A deferred constraint trigger validates at transaction commit that every journal has at least two postings, posting currency equals journal currency, and debit total equals credit total. Successful captures/refunds must reference their journal in the same finalizer transaction.

## Events and Webhook Delivery

### `webhook_endpoints`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `name text` not null
- `status text` not null (`ACTIVE`, `DISABLED`)
- `created_at timestamptz` not null
- unique `(organisation_id, id)`

### `webhook_endpoint_versions`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `webhook_endpoint_id uuid` not null
- `version integer` not null, check positive
- `url text` not null; sandbox allowlist limits it to bundled receiver
- `encrypted_secret bytea` not null
- `subscribed_event_types text[]` not null
- `active_from timestamptz` not null
- `active_until timestamptz` null
- `created_at timestamptz` not null
- composite FK to endpoint within organisation
- unique `(organisation_id, id)`
- unique `(webhook_endpoint_id, version)`
- immutable after insert except one-way `active_until` closure

### `merchant_events`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `payment_intent_id uuid` not null
- `provider_operation_id uuid` not null
- `event_type text` not null (`payment.authorized.v1`, `payment.captured.v1`, `refund.succeeded.v1`)
- `schema_version integer` not null
- `event_bytes bytea` not null
- `event_sha256 bytea` not null
- `occurred_at timestamptz` not null
- `created_at timestamptz` not null
- composite FKs within organisation
- unique `(provider_operation_id, event_type)`
- append-only/immutable trigger
- index `(organisation_id, payment_intent_id, occurred_at)`

### `event_recipients`

- `id uuid` primary key
- `organisation_id uuid` not null
- `merchant_event_id uuid` not null
- `endpoint_version_id uuid` not null
- `created_at timestamptz` not null
- composite FKs within organisation
- unique `(merchant_event_id, endpoint_version_id)`
- append-only/immutable trigger
- index `(merchant_event_id, id)` for materialization

The finalizer inserts the event and all active subscribed endpoint-version recipients in the same transaction.

### `webhook_deliveries`

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `event_recipient_id uuid` not null
- `replay_of_delivery_id uuid` null
- `status text` not null (`PENDING`, `DELIVERING`, `RETRY_WAIT`, `DELIVERED`, `DEAD_LETTER`)
- `attempt_count integer` not null default 0
- `next_attempt_at timestamptz` not null
- `lease_token uuid` null
- `lease_expires_at timestamptz` null
- `delivered_at timestamptz` null
- `dead_lettered_at timestamptz` null
- `created_at timestamptz` not null
- `updated_at timestamptz` not null
- composite FK to recipient within organisation
- FK `replay_of_delivery_id` → delivery
- unique `(event_recipient_id)` where `replay_of_delivery_id IS NULL`
- index `(status, next_attempt_at)` for claim polling
- index `(lease_expires_at)` where status is `DELIVERING`
- checks keep lease fields/status and terminal timestamps consistent

### `webhook_delivery_attempts`

- `id uuid` primary key
- `organisation_id uuid` not null
- `webhook_delivery_id uuid` not null
- `sequence integer` not null, check positive
- `lease_token uuid` not null
- `request_timestamp bigint` not null
- `event_sha256 bytea` not null
- `response_http_status smallint` null
- `result text` not null (`ACKNOWLEDGED`, `RETRYABLE`, `PERMANENT`, `TRANSPORT_ERROR`)
- `safe_error_code text` null
- `started_at timestamptz` not null
- `completed_at timestamptz` null
- composite FK to delivery within organisation
- unique `(webhook_delivery_id, sequence)`
- append-only/immutable trigger

## Scenario Correlation

Scenario progress should be reconstructed from real domain evidence where practical. If asynchronous UI polling requires a durable handle, use a minimal table rather than duplicating business truth.

### `demo_scenario_runs` (optional, console only)

- `id uuid` primary key
- `public_id text` unique, not null
- `organisation_id uuid` not null
- `scenario_type text` not null
- `payment_intent_id uuid` null until created
- `status text` not null (`RUNNING`, `COMPLETED`, `NEEDS_INSPECTION`)
- `fault_config jsonb` not null; allowlisted synthetic controls only
- `started_at timestamptz` not null
- `completed_at timestamptz` null
- composite FK to payment when present
- index `(organisation_id, started_at DESC)`

Do not use this table to decide payment/provider state. It is a correlation handle and read model only.

## Mock Provider Database

The provider uses a separate database and role.

### `provider_accounts`

- `id uuid` primary key
- `public_id text` unique, not null
- `name text` not null
- `signing_secret_digest bytea` not null or secret supplied through environment
- `created_at timestamptz` not null

### `provider_effects`

- `id uuid` primary key
- `provider_account_id uuid` not null
- `stable_key text` not null
- `operation_kind text` not null
- `reference text` not null
- `amount bigint` not null, check positive
- `currency text` not null, check INR
- `request_sha256 bytea` not null
- `outcome text` not null (`PENDING`, `SUCCEEDED`, `DECLINED`)
- `decline_code text` null
- `response_bytes bytea` null
- `created_at timestamptz` not null
- `completed_at timestamptz` null
- unique `(provider_account_id, stable_key)` guaranteeing one effect
- check re-use with contradictory request fields is rejected, never aliased

### `provider_fault_directives`

- `id uuid` primary key
- `provider_account_id uuid` not null
- `stable_key text` not null
- `fault_type text` not null; allowlisted deterministic test faults
- `remaining_uses integer` not null default 1
- `created_at timestamptz` not null
- unique `(provider_account_id, stable_key, fault_type)`

Fault directives may alter response delivery but must not bypass `provider_effects` uniqueness.

## Receiver Store

The receiver uses a dedicated `receiver` schema and least-privilege role inside the `relaypay` database. This preserves the frozen two-database topology while keeping deduplication state outside RelayPay application-role access.

### `received_events`

- `event_id text` primary key
- `event_sha256 bytea` not null
- `first_received_at timestamptz` not null
- `last_received_at timestamptz` not null
- `delivery_count integer` not null default 1
- `signature_timestamp bigint` not null

On duplicate event ID with the same digest, increment count and acknowledge without repeating the consumer effect. A different digest for an existing event ID is rejected and surfaced as contradictory evidence.

## Access Rules

### Organisation Administrator Session

- Read all listed resources for its own organisation.
- Run allowlisted demo scenarios for its own organisation.
- Request status-only lookup for its own review operation.
- Optionally replay its own webhook delivery.
- Cannot assert provider outcome, mutate journals/events/attempts, access secrets, or cross tenant boundaries.

### Merchant API Key

- Create customer/payment and issue payment commands within granted scopes and organisation.
- Read payment/operation public envelopes within organisation.
- Cannot call evidence, retry-lookup, delivery-detail, replay, identity, or configuration endpoints.

### Worker Roles

- Recovery role: claim/update operations and append attempts/history; call shared finalizer.
- Materializer role: read immutable events/recipients and insert missing deliveries.
- Delivery role: claim deliveries, read encrypted endpoint secret via application decryptor, append attempts, update delivery state.
- No role receives provider-database credentials except the provider service.

## Sensitive Data Rules

- Passwords use a modern password hash; API keys use peppered digest; session/CSRF tokens use digests.
- Webhook secrets are encrypted at rest with an application-managed key; signing secrets never enter logs/evidence.
- Store raw provider response bytes only if the threat model permits and they contain synthetic allowlisted fields; otherwise store encrypted bytes plus sanitized evidence.
- Idempotency keys, authorization headers, cookies, CSRF values, peppers, signing material, and plaintext secrets are never logged.
- No real PAN, bank account, government ID, address, or personal financial data is accepted.

## Required Database Invariants

- Composite tenant foreign keys prevent cross-organisation relationships.
- Singleton authorization/capture constraints select one winner under races.
- Stable provider key and provider-effect uniqueness prevent duplicate provider mutations.
- Nonterminal idempotency records always reference an operation.
- Terminal operation/idempotency responses have exact bytes, digest, and status.
- Refund reservation states cannot be excluded from availability calculations.
- Successful capture/refund references exactly one balanced posted journal.
- Posted journals, postings, events, recipients, and attempt history are immutable.
- One event per operation/type and one initial delivery per recipient.
- Expired recovery/delivery leases remain selectable.
