# Resilience proofs

Every resilience property below is demonstrated by an executable test in
the canonical suite (and therefore in the CI release gate). This table maps each requirement to its
proof; nothing on this list is documentation-only.

| Requirement | Proof (test files) | Mechanism exercised |
| --- | --- | --- |
| No task-queue dependency | `tests/unit/test_recovery_worker_config.py`, `tests/unit/test_m7_failure_contracts.py` | there is no task queue or Redis to lose: one poller owns every batch, and PostgreSQL is the authority for operation state, leases, and delivery progress. Redpanda carries only the derived outbox stream |
| Worker shutdown | `tests/unit/test_recovery_worker_config.py`, `tests/integration/test_week3_provider_recovery.py` | a crashed poller restarts and reclaims leased work: claims carry lease tokens with expiry under `SKIP LOCKED`, and expired leases are re-issued |
| Redpanda loss / redelivery dedupe | `tests/integration/test_m10_agent_runtime.py` | `record_consumption`/`consumed_business_events` dedupe makes redelivery idempotent |
| Consumer dedupe, version pinning, budget gate | `tests/integration/test_m10_agent_runtime.py` | one consumer effect per event id, pinned schema versions, token/cost ceilings (exercised against the leased step engine, which no API or poller path calls as of 1.0.1) |
| DB lease reclaim | `tests/integration/test_week3_provider_recovery.py` | expired `lookup_lease_expires_at` is reclaimed with a fresh lease token under `SKIP LOCKED` |
| Provider outage + fallback | `tests/unit/test_agent_runtime.py` | `ProviderRouter` fails over only on `RetryableProviderError` |
| Fallback exhaustion | `tests/unit/test_provider_router.py` | all providers failing raises `all configured model providers failed`; empty router rejected |
| Terminal errors never fall back | `tests/unit/test_provider_router.py` | schema-violating responses halt instead of rerouting to another vendor |
| DLQ replay | `tests/integration/test_week4_delivery.py` | retry budget exhausts into the dead-letter queue without mutating event bytes |
| Approval waiting / maker-checker | `tests/integration/test_m11_disputes.py` | artifact edits invalidate the pending approval; ambiguous submit produces exactly one effect |
| PII redaction | `tests/unit/test_agent_runtime.py`, `tests/unit/test_subscription_intake.py`, `packages/relaypay/subscriptions/intake.py` (`assert_pii_free_evidence`) | PII tokenized before prompts; evidence containing PII rejected with `EVENT_CONTAINS_PII` |
| Prompt injection | `tests/unit/test_agent_runtime.py`, `tests/unit/test_settlement_classification.py`, `tests/integration/test_m14_risk_review.py` | untrusted-evidence delimiters cannot be closed by content; deterministic checks quote verbatim snapshot text only |
| Tenant / environment isolation | `tests/integration/test_tenant_and_role_boundaries.py`, `tests/integration/test_m14_risk_review.py` | composite tenant keys, `404` for foreign IDs, scope predicates on every read |
| Cross-workflow adversarial suite | `tests/fixtures/evaluations/v1/adversarial-v1.json` via `scripts/run_evaluations.py` | 20 adversarial cases (prompt injection, cross-tenant access, PII, permission escalation, duplicate effects) with zero violations, asserted in CI |

## v2.0 proof harness (M15)

These proofs run in the CI `proof-gate` job (`pytest -m "(proof or chaos) and not handwritten"`
plus `make chaos`). Invariant evaluation waits for the maintainer's hand-written checker (see
[PROVENANCE.md](../../PROVENANCE.md)) and runs in `handwritten-gate`.

| Requirement | Proof (test files) | Mechanism exercised |
| --- | --- | --- |
| Generated operation sequences keep money consistent | `tests/integration/test_stateful_payments.py` | Hypothesis state machine over create, authorize, capture, refund, replay, key reuse, recovery and pending resolution, with lost, declined and pending provider outcomes; journals balance, refunds ≤ captured, and one provider mutation per stable key after every step |
| Refunds never exceed captured, sequentially or concurrently | `tests/integration/test_property_refunds.py` | random partial-refund lists, 8 concurrent workers, exact `REFUND_AMOUNT_EXCEEDS_AVAILABLE` rejections |
| No payout overdraft under contention | `tests/integration/test_payout_500_way.py` | 500 concurrent reservations against a balance that fits exactly 250 (with `compose.proof.yaml`) |
| Duplicate HTTP requests are applied once | `tests/integration/test_http_duplicate_requests.py` | 50 concurrent identical POSTs through a real uvicorn server: one row, byte-identical bodies, and exactly one provider mutation for capture |
| Crash at any protocol step converges | `tests/integration/test_chaos_ci.py`, `make chaos` | seeded schedules with crash points after send preparation, after the provider response, after finalizer commit, around payout bank sends and between poller tasks; quiesce, then snapshot, then the invariant check |
| Evidence tables from 0012–0017 are immutable | `tests/integration/test_m15_immutability.py`, `scripts/verify_m15_upgrade.py` | full-row and column-scoped triggers (SQLSTATE 55000), with checksums preserved across upgrade |
| Open: refund cannot spend reserved payout funds (F1) | `tests/integration/test_f1_refund_vs_reserved_payout.py` (strict xfail) | fix scheduled for M16 |
