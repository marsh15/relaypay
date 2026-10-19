# Database

All data lives in one PostgreSQL 17 server, split into four databases plus one isolated schema.
Models are SQLAlchemy 2 declarative classes under `packages/relaypay/*/models.py`; schema changes
are Alembic revisions under `migrations/`. The frozen Phase 1 design in
[vibe-coding/05-backend-schema.md](vibe-coding/05-backend-schema.md) is superseded by this
document; it predates environments, memberships, bank, commerce, and everything after M2.

## Databases, roles, and migrations

`infra/postgres/init/00-roles-and-databases.sh` creates the roles and databases on the first start
of an empty data volume. It does not run again.

| Database | Migrator role | App role | Alembic tree | Head revision |
| --- | --- | --- | --- | --- |
| `relaypay` | `relaypay_migrator` | `relaypay_app` | `migrations/relaypay` | `0017_merchant_risk_review` |
| `provider` | `provider_migrator` | `provider_app` | `migrations/provider` | `0003_slow_fault` |
| `bank` | `bank_migrator` | `bank_app` | `migrations/bank` | `0001_bank` |
| `commerce` | `commerce_migrator` | `commerce_app` | `migrations/commerce` | `0001_commerce` |
| `relaypay`, schema `receiver` | `receiver_app` (owner) | `receiver_app` | `migrations/receiver` | `0001_receiver` |

- `CONNECT` is revoked from `PUBLIC` on every database. `CREATE` on schema `public` is revoked
  from `PUBLIC`. App roles get DML on tables the migrator creates, through default privileges.
- The `receiver` schema is created `AUTHORIZATION receiver_app` and closed to the RelayPay roles;
  its `alembic_version` table lives in that schema. There is no separate receiver migrator.
- App roles have no DDL and no `TRUNCATE`. Row immutability is enforced by triggers, not by
  revoking `UPDATE`/`DELETE` (see [Immutability](#immutability)).
- Every Python container receives every database URL, including the migrator URLs. The role
  separation is a database property, not a per-container one.

The `relaypay` revision chain is linear: `0001_foundation`, `0002_payments`, `0003_events`,
`0004_delivery`, `0005_scenarios`, `0006_identity_environments`, `0007_reconciliation`,
`0008_statement_currency`, `0009_merchant_balances`, `0010_payouts`, `0011_connectors`,
`0012_observability`, `0013_agent_runtime`, `0014_disputes`, `0015_subscription_recovery`,
`0016_settlement_intelligence`, `0017_merchant_risk_review`. Revision `0004_delivery` lives in
`99ecfc616d22_webhook_endpoint_versions_recipients_.py` (an autogenerate filename that was never
renamed), so the files do not sort in chain order. Provider is `0001_provider`,
`0002_provider_statements`, `0003_slow_fault`.

### Running migrations

```bash
make migrate     # upgrade head on all five trees, in the order relaypay, provider, receiver, bank, commerce
make seed        # two demo organisations, API keys, endpoints, simulator accounts
```

`make migrate` ignores `.env`. `env.py` reads `*_MIGRATION_DATABASE_URL` from the process
environment and otherwise falls back to the `sqlalchemy.url` in `alembic.ini` (development
passwords on `localhost:55432`). The receiver tree reads `RECEIVER_DATABASE_URL` instead. The bank
and commerce trees must be run from the repository root. In Compose, the `migrate` service runs
the five upgrades (relaypay, provider, bank, commerce, receiver) and then `scripts.seed`. CI also
runs `alembic ... check` on each tree to detect model drift.

## Tenancy model

Every tenant-owned table carries `organisation_id` and `environment_id` with composite foreign keys
to `environments(organisation_id, id)`. An organisation always has two environments, `TEST` and
`LIVE_LIKE`; there is no environment named `LIVE`. Uniqueness constraints are scoped the same way,
for example `(organisation_id, environment_id, merchant_reference)`. Row-level security is not
used: isolation comes from query predicates and composite keys, and a foreign identifier returns
`404`.

## Tables in the `relaypay` database

About 90 tables, grouped by package.

| Domain (package) | Tables |
| --- | --- |
| Identity (`identity`) | `organisations`, `environments`, `users`, `organisation_memberships`, `sessions`, `api_keys`, `api_key_versions`, `audit_records` |
| Payments (`payments`) | `customers`, `payment_intents`, `authorizations`, `captures`, `refunds` |
| Operations and idempotency (`provider_operations`) | `provider_operations`, `provider_attempts`, `operation_history`, `idempotency_records` |
| Ledger (`ledger`) | `ledger_accounts`, `journals`, `postings` |
| Merchant balances (`merchant_balances`) | `merchant_accounts`, `settlement_runs`, `settlement_items`, `balance_transactions` |
| Payouts (`payouts`) | `beneficiaries`, `payouts`, `payout_reservation_history`, `payout_attempts`, `payout_attempt_evidence`, `payout_history`, `payout_events` |
| Events and webhooks (`event_delivery`) | `webhook_endpoints`, `webhook_endpoint_versions`, `merchant_events`, `event_recipients`, `webhook_deliveries`, `webhook_delivery_attempts` |
| Reconciliation (`reconciliation`) | `statement_imports`, `statement_items`, `reconciliation_runs`, `reconciliation_matches`, `reconciliation_mismatches`, `mismatch_evidence_versions`, `mismatch_workflow_history` |
| Connectors (`connectors`) | `connectors`, `connector_versions`, `connector_credential_versions`, `connector_health_observations`, `inbound_webhook_events`, `inbound_webhook_attempts` |
| Observability (`observability`) | `request_logs`, `usage_rollups` |
| Agent runtime (`agent_runtime`) | `business_event_outbox`, `consumed_business_events`, `workflow_definitions`, `workflow_runs`, `workflow_steps`, `workflow_artifacts`, `approval_requests`, `approval_decisions`, `workflow_dead_letters`, `prompt_versions`, `pricing_versions`, `model_invocations`, `tool_invocations`, `evaluation_datasets`, `evaluation_runs` |
| Disputes (`disputes`) | `dispute_cases`, `dispute_draft_versions`, `dispute_package_versions`, `dispute_submission_attempts` |
| Subscription recovery (`subscriptions`) | `subscriptions`, `subscription_invoices`, `recurring_payment_attempts`, `recovery_policy_versions`, `recovery_cases`, `scheduled_recovery_actions`, `communication_records`, `recovery_opt_outs` |
| Settlement intelligence (`settlement_intelligence`) | `settlement_policies`, `settlement_forecasts`, `settlement_forecast_items`, `settlement_questions` |
| Risk review (`risk_review`) | `onboarding_snapshots`, `risk_reviews`, `risk_review_versions`, `risk_findings`, `risk_escalations`, `risk_annotations` |
| Demo (`demo_scenarios`) | `scenario_runs` (only `LOST_CAPTURE_RESPONSE`) |

`analytics`, `evidence`, and `operations` define no tables; they are read-only query layers.
`tool_invocations` is defined but nothing writes to it.

### Simulator databases

| Database | Tables |
| --- | --- |
| `provider` | `provider_accounts`, `provider_effects` (unique `(account, stable_key)`), `provider_fault_directives`, `provider_statement_exports`, `provider_statement_items` |
| `bank` | `bank_accounts`, `bank_effects` (unique `(account, stable_key)`), `bank_fault_directives` |
| `commerce` | `commerce_accounts`, `commerce_orders`, `commerce_payment_links` |
| `relaypay.receiver` | `received_events` (primary key `event_id`, with `event_sha256` and `delivery_count`) |

## Constraints that carry correctness

- **One effect per stable key.** `provider_operations` is unique on
  `(organisation_id, environment_id, stable_provider_key)` and on `(…, kind, resource_id)`.
  `provider_attempts` has a partial unique index allowing exactly one `MUTATION` attempt per
  operation. The provider simulator is independently unique on `(account, stable_key)`.
- **Idempotency.** `idempotency_records` is unique on `(organisation_id, environment_id,
  key_digest)`. Keys are stored only as an HMAC-SHA256 digest with a redacted hint. A check
  constraint requires terminal rows to carry status, bytes, and digest, and non-terminal rows to
  reference an operation. There is no expiry.
- **Singletons.** One authorization and one capture per payment intent; capture and refund rows
  each have a unique `provider_operation_id` and `journal_id`.
- **One settlement per capture.** `settlement_items` is unique on `capture_id` and `journal_id`.
- **One initial delivery per recipient.** A partial unique index on `webhook_deliveries` where
  `replay_of_delivery_id IS NULL`; replays are separate rows.
- **Merchant event types** are limited by a check to `payment.authorized.v1`,
  `payment.captured.v1`, and `refund.succeeded.v1`. Payout events use `payout_events`.
- **Maker-checker.** `approval_decisions` checks `decision_maker_user_id <> maker_user_id`.
- **Bounded recovery.** `recovery_cases.payment_retry_count` and `message_count` are each
  limited to 0 to 3.

## Ledger

Tables: `ledger_accounts`, `journals`, `postings`. Amounts are integer paise; currency is `INR`.

| Account code | Scope | Meaning |
| --- | --- | --- |
| `PROVIDER_CLEARING_ASSET` | environment | Value held with the provider; created by `scripts/seed.py` and demo scripts, not by migrations |
| `PENDING_PAYABLE_LIABILITY` | per merchant account | Captured, unsettled merchant value |
| `AVAILABLE_PAYABLE_LIABILITY` | per merchant account | Settled value eligible for payout |
| `MERCHANT_RECEIVABLE_ASSET` | per merchant account | Refund value owed by the merchant beyond payable balances |
| `PAYOUT_CLEARING_ASSET` | per merchant account | Value in transit to the bank |
| `MERCHANT_PAYABLE_LIABILITY` | environment (legacy) | Pre-M3 account; migration 0009 moved its balance into pending payable through an `OPENING` journal. No current code posts to it |

The four per-merchant accounts are created automatically when a merchant account is created.

| Event | Journal (debit / credit) |
| --- | --- |
| Capture | `PROVIDER_CLEARING_ASSET` / `PENDING_PAYABLE_LIABILITY` |
| Refund | Pending payable, then available payable, then merchant receivable for the remainder / `PROVIDER_CLEARING_ASSET` |
| Settlement | `PENDING_PAYABLE_LIABILITY` / `MERCHANT_RECEIVABLE_ASSET` (offset) and `AVAILABLE_PAYABLE_LIABILITY` |
| Payout (verified bank success only) | `AVAILABLE_PAYABLE_LIABILITY` / `PAYOUT_CLEARING_ASSET` |

Authorization posts nothing. Journal types allowed by the database are `CAPTURE`, `REFUND`,
`COMPENSATION`, `OPENING`, `SETTLEMENT`, `PAYOUT`; no code path currently posts a `COMPENSATION`
journal, although reconciliation can link one when resolving a mismatch.

Balance is enforced twice. `post_journal` rejects unbalanced or non-positive entries in Python.
In the database, deferred constraint triggers on `journals` and `postings` check at commit that
there are at least two postings, debits equal credits, and every posting's currency matches the
journal's. A further deferred trigger ties `CAPTURE` and `REFUND` journals to the matching
provider operation and amount. Balances are always derived from postings;
`balance_transactions` is an immutable per-journal projection, not an authority.

## Immutability

Evidence tables are protected by `BEFORE UPDATE OR DELETE` triggers that raise an error.

- **Trigger-protected** (migrations 0001 to 0011): `journals`, `postings`, `audit_records`,
  `merchant_events`, `event_recipients`, `webhook_delivery_attempts`, `webhook_endpoint_versions`
  (only `active_until` may be set, once), `operation_history`, `provider_attempts` (a `SENT`
  attempt may complete once; no delete), `statement_imports`, `statement_items`,
  `reconciliation_matches`, `mismatch_evidence_versions`, `mismatch_workflow_history`,
  `settlement_items`, `balance_transactions`, the payout reservation, evidence, history, and event
  tables, the connector credential, health, and inbound-attempt tables, and the payload columns of
  `inbound_webhook_events`. In the provider database: `provider_statement_exports`,
  `provider_statement_items`, and the identity columns of `provider_effects`.
- **Trigger-protected, migration 0018** (tables from migrations 0012 to 0017 that code never
  rewrites; classification and evidence are in the `0018_immutability_backfill` docstring):
  - Full-row (`relaypay_prevent_evidence_mutation`): `pricing_versions`,
    `consumed_business_events`, `evaluation_datasets`, `prompt_versions`, `workflow_artifacts`,
    `model_invocations`, `tool_invocations`, `approval_decisions`, `dispute_draft_versions`,
    `recurring_payment_attempts`, `recovery_opt_outs`, `settlement_forecasts`,
    `settlement_forecast_items`, `onboarding_snapshots`, `risk_review_versions`,
    `risk_findings`, `risk_annotations`.
  - Column-scoped (`relaypay_prevent_frozen_column_mutation`; `DELETE` rejected, only the listed
    columns may change): `workflow_definitions` and `settlement_policies` and
    `recovery_policy_versions` and `dispute_package_versions` (`status`), `evaluation_runs`
    (`status`, `result`, `result_sha256`, `completed_at`), `workflow_dead_letters`
    (`replay_count`, `last_replayed_at`), `communication_records` (`status`, `response_code`,
    `response_sha256`).
- **Application-level only** (mutable workflow state, deliberately unprotected): `request_logs`
  (retention prune), `usage_rollups` (upsert), `business_event_outbox`, `workflow_runs`,
  `workflow_steps`, `approval_requests` (leases and status machines), `dispute_cases`,
  `dispute_submission_attempts`, `subscriptions`, `subscription_invoices`, `recovery_cases`,
  `scheduled_recovery_actions`, `settlement_questions`, `risk_reviews`, `risk_escalations`, and
  the bank and commerce databases. A privileged connection can `UPDATE` them.
- `TRUNCATE` is not blocked by row triggers. `scripts/reset_sandbox.py` relies on this and
  connects with the migrator role.

## Seeding and reset

`scripts/seed.py` is idempotent per demo user. For each new organisation it creates a user, an
`ORGANISATION_ADMIN` membership, a TEST API key (scopes `customers:write`, `payments:read`,
`payments:write`; printed once), the global ledger accounts, a bundled-receiver webhook endpoint,
a subscription-recovery demo, settlement defaults, and risk-review snapshots, then provider, bank,
and commerce accounts in their own databases. The TEST and LIVE_LIKE environments and the default
merchant account come from ORM `before_flush` hooks and a database trigger.

`scripts/reset_sandbox.py` requires `SANDBOX_RESET_CONFIRM=RESET_SYNTHETIC_RELAYPAY` (and
`ALLOW_SANDBOX_RESET=true` when `APP_ENV=production`), truncates `receiver.received_events`, then
`organisations`, `provider_accounts`, `bank_accounts`, and `commerce_accounts` with `CASCADE`, and
reseeds. It is coordinated but not atomic across the databases, so stop writers first.
