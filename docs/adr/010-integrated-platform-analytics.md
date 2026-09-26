# Integrated platform analytics stay in PostgreSQL with low-cardinality gauges only

Status: accepted for v1.0.0. Cross-workflow analytics are computed by pure SQL aggregates over the
existing authoritative tables — dispute cases, draft and package versions, subscription recovery
cases and actions, workflow runs and steps, model invocations, approvals, and risk reviews — inside
one analytics module. No new tables, no event-store duplication, and no analytics pipeline exist:
the database remains the only durable authority, and every metric is reproducible from immutable
rows. Handling-time baselines and the analytics shape are versioned in code
(`ANALYTICS_VERSION`, `MANUAL_TIME_BASELINES_MINUTES`) so historical recomputations stay
explainable.

Tenant and environment dimensions are scope predicates on every query and never Prometheus labels.
The six portfolio gauges (`relaypay_portfolio_completion_rate{kind=raw|approval_adjusted}`,
`..._recovery_rate`, `..._handling_time_reduction`, `..._model_cost_usd_micros`,
`..._analyst_intervention_rate`, `..._invalid_action_rate`) are low-cardinality series refreshed
explicitly; tenant identity lives in PostgreSQL where it can be joined, audited, and deleted.

Release correctness is pinned by a versioned evaluation suite: fixtures are committed
(`tests/fixtures/evaluations/v1/`), regenerated only by the checked-in generator, and replayed by
`scripts/run_evaluations.py` through the real domain services with deterministic providers. The
runner asserts evidence-retrieval precision ≥ 0.90, zero policy/numeric/citation violations, zero
unexpected escalations, and zero adversarial effects (prompt injection, cross-tenant access, PII in
evidence, permission escalation, duplicate effects), records an `EvaluationDataset` and
`EvaluationRun`, and runs as a CI release gate. Live-provider comparison is deliberately
release-only and key-gated so vendor quality can be compared without making CI depend on external
services, credentials, or non-determinism.
