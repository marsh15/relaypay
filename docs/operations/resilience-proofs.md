# Resilience proofs — v1.0.0

Every resilience property required by the v1.0.0 mandate is demonstrated by an executable test in
the canonical suite (and therefore in the CI release gate). This table maps each requirement to its
proof; nothing on this list is documentation-only.

| Requirement | Proof (test files) | Mechanism exercised |
| --- | --- | --- |
| Redis loss tolerance | `tests/unit/test_recovery_worker_config.py` | poller is broker-less; PostgreSQL is the authority for operation state, leases, and delivery progress; Redis is acceleration only |
| Celery loss / worker shutdown | `tests/unit/test_recovery_worker_config.py` | `acks_late` and `task_reject_on_worker_lost` configuration asserts so an interrupted worker redelivers |
| Redpanda loss / redelivery dedupe | `tests/integration/test_m10_agent_runtime.py` | `record_consumption`/`consumed_business_events` dedupe makes redelivery idempotent |
| Broker loss dedupe, version pinning, budget gate | `tests/integration/test_m10_agent_runtime.py` | one consumer effect per event id, pinned schema versions, token/cost ceilings |
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
