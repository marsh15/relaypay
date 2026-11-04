# Resilience proofs

Each resilience property below is mapped to an executable test in the canonical suite, and the
suite runs in the CI release gate. The 15 money invariants are evaluated by
`packages/relaypay/proof/invariants.py` on every chaos seed and by `tests/unit/test_invariants_spec.py`.
The checker was written by Claude at the maintainer's request; see
[PROVENANCE.md](../../PROVENANCE.md). Its first run on real chaos data found a double-finalization bug
(a waiting finalizer reused a stale operation status), now fixed and covered by
`tests/integration/test_finalizer_locked_read.py`.

| Requirement | Proof (test files) | Mechanism exercised |
| --- | --- | --- |
| No task-queue dependency | `tests/unit/test_recovery_worker_config.py`, `tests/unit/test_m7_failure_contracts.py` | there is no task queue or Redis to lose: one poller owns every batch, and PostgreSQL is the authority for operation state, leases, and delivery progress. Redpanda carries only the derived outbox stream |
| Worker shutdown | `tests/unit/test_recovery_worker_config.py`, `tests/integration/test_week3_provider_recovery.py` | a crashed poller restarts and reclaims leased work: claims carry lease tokens with expiry under `SKIP LOCKED`, and expired leases are re-issued |
| Redpanda loss / redelivery dedupe | `tests/integration/test_m10_agent_runtime.py` | `record_consumption`/`consumed_business_events` dedupe makes redelivery idempotent. The test covers the consumer-side dedupe helper; no consumer of the stream exists in this repository |
| Consumer dedupe, version pinning, budget gate | `tests/integration/test_m10_agent_runtime.py` | one consumer effect per event id, pinned schema versions, token/cost ceilings (exercised against the leased step engine, which no API or poller path calls at this checkout) |
| DB lease reclaim | `tests/integration/test_week3_provider_recovery.py` | expired `lookup_lease_expires_at` is reclaimed with a fresh lease token under `SKIP LOCKED` |
| Provider outage + fallback | `tests/unit/test_agent_runtime.py` | `ProviderRouter` fails over only on `RetryableProviderError` |
| Fallback exhaustion | `tests/unit/test_provider_router.py` | all providers failing raises `all configured model providers failed`; empty router rejected |
| Terminal errors never fall back | `tests/unit/test_provider_router.py` | schema-violating responses halt instead of rerouting to another vendor |
| DLQ replay | `tests/integration/test_week4_delivery.py` | retry budget exhausts into the dead-letter queue without mutating event bytes |
| Merchant webhook retry schedule | `tests/unit/test_webhook_backoff.py` | jittered delay within the doubling ceiling, reproducible per delivery and attempt, capped, and the full schedule rides out minutes of receiver failure |
| Approval waiting / maker-checker | `tests/integration/test_m11_disputes.py` | artifact edits invalidate the pending approval; ambiguous submit produces exactly one effect |
| PII redaction | `tests/unit/test_agent_runtime.py`, `tests/unit/test_subscription_intake.py`, `packages/relaypay/subscriptions/intake.py` (`assert_pii_free_evidence`) | PII tokenized before prompts; evidence containing PII rejected with `EVENT_CONTAINS_PII` |
| Prompt injection | `tests/unit/test_agent_runtime.py`, `tests/unit/test_settlement_classification.py`, `tests/integration/test_m14_risk_review.py` | untrusted-evidence delimiters cannot be closed by content; deterministic checks quote verbatim snapshot text only |
| Tenant / environment isolation | `tests/integration/test_tenant_and_role_boundaries.py`, `tests/integration/test_m14_risk_review.py` | composite tenant keys, `404` for foreign IDs, scope predicates on every read |
| Cross-workflow adversarial suite | `tests/fixtures/evaluations/v1/adversarial-v1.json` via `scripts/run_evaluations.py` | 20 adversarial cases (prompt injection, cross-tenant access, PII, permission escalation, duplicate effects) with zero violations, asserted in CI |

## v2.0 proof harness (M15, extended in M16 and M17)

The CI `proof-gate` job runs `pytest -m "(proof or chaos) and not handwritten"` and `make chaos`.
The specification tests for the four core modules run in `handwritten-gate`.

`make chaos` fails on harness failures and reports invariant violations per seed: unexpected exceptions, operations that do not converge after quiesce, or a broken
provider exactly-once check. The per-run `findings` counter is not incremented by any current
code path, so it is always empty. See [the runbook](runbook.md#chaos-runs) for how to read a
failing seed.

| Requirement | Proof (test files) | Mechanism exercised |
| --- | --- | --- |
| Generated operation sequences keep money consistent | `tests/integration/test_stateful_payments.py` | Hypothesis state machine over create, authorize, capture, refund, replay, key reuse, recovery and pending resolution, with lost, declined and pending provider outcomes; journals balance, refunds ≤ captured, and one provider mutation per stable key after every step. These are assertions inside the test, not the maintainer's invariant checker |
| Refunds never exceed captured, sequentially or concurrently | `tests/integration/test_property_refunds.py` | random partial-refund lists, 8 concurrent workers, exact `REFUND_AMOUNT_EXCEEDS_AVAILABLE` rejections |
| No payout overdraft under contention | `tests/integration/test_payout_500_way.py` | 500 concurrent reservations against a balance that fits exactly 250 (with `compose.proof.yaml`) |
| Refund cannot spend reserved payout funds (F1) | `tests/integration/test_f1_refund_vs_reserved_payout.py`, `tests/integration/test_payout_overdraft_guard.py` | closed in M16. Refunds draw only on AVAILABLE not reserved for payouts (`419ab3f`). Migration 0020's deferred trigger rejects any reservation or AVAILABLE debit that would overdraw at commit (`5cd01a4`) |
| Duplicate HTTP requests are applied once | `tests/integration/test_http_duplicate_requests.py` | 50 concurrent identical POSTs through a real uvicorn server: one row, byte-identical bodies, and exactly one provider mutation for capture |
| Crash at any protocol step converges | `tests/integration/test_chaos_ci.py`, `make chaos` | seeded schedules with crash points after send preparation, after the provider response, after finalizer commit, around payout bank sends and between poller tasks; quiesce, then snapshot. The invariant check is not evaluated (see above) |
| Evidence tables from 0012–0017 are immutable | `tests/integration/test_m15_immutability.py`, `scripts/verify_m15_upgrade.py` | full-row and column-scoped triggers (SQLSTATE 55000), with checksums preserved across upgrade |

## PSP webhooks and provider chaos (M17)

These cover the inbound PSP inbox, the applier, and the provider simulator's chaos and push. The
ordering decision applies, ignores or reviews each event by operation state. See
[ADR-012](../adr/012-webhooks-are-evidence-not-a-money-path.md).

| Requirement | Proof (test files) | Mechanism exercised |
| --- | --- | --- |
| Signed PSP webhooks are stored once; redelivery replays and conflicting bytes are refused | `tests/integration/test_psp_inbox.py` | verified events matched to their operation; unknown stable key kept as `UNMATCHED`; unauthenticated input refused before parsing; stale and body-id-mismatched events refused; concurrent duplicate deliveries store one row; the HTTP route accepts and replays |
| Raw inbox bytes never change; only the lifecycle moves | `tests/integration/test_psp_inbox.py` | column-scoped immutability on `psp_events` |
| Applied webhooks finalize only through the one finalizer | `tests/integration/test_psp_applier.py` | a verified event is recorded as a `WEBHOOK` provider attempt and finalized by the finalizer; a crash after recording resumes without a second attempt |
| Without the ordering decision, events are deferred, not applied | `tests/integration/test_psp_applier.py` | `DEFERRED` with `ORDERING_DECISION_UNAVAILABLE`; polling remains the guarantee |
| Mismatched evidence goes to review; repeated failures dead-letter | `tests/integration/test_psp_applier.py` | an identity mismatch moves the operation to `REQUIRES_REVIEW`; an event that keeps failing is dead-lettered, and one failing event does not stall the batch |
| Provider chaos is seeded and replayable | `tests/unit/test_provider_chaos.py`, `tests/integration/test_provider_chaos_webhooks.py` | each decision is a pure function of seed, stable key and aspect; pending takes precedence over decline; one-shot directives win over the profile; the same seed replays the same outcomes |
| Provider push delivers each event at least once, with drop, duplicate and delay | `tests/integration/test_provider_chaos_webhooks.py` | signed events RelayPay accepts; duplicates are sent twice with the same bytes; dropped events are kept but never sent; failed pushes back off and then dead-letter |

## Not yet proven

- **Money invariants.** The 15 invariants are not evaluated by any test or chaos run until the
  maintainer's checker (`packages/relaypay/proof/invariants.py`) is implemented.
- **PSP ordering.** `packages/relaypay/psp/ordering.py::decide` is pending, so PSP events are
  `DEFERRED` and do not change operation state.
- **Provider webhook chaos in the chaos harness.** Provider webhook drop, duplicate and delay are
  not yet fed into the chaos harness, so the harness does not exercise them.
