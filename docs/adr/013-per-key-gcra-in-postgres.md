# ADR-013: Per-key rate limiting is GCRA in PostgreSQL and fails open

Status: accepted (v2.0, M19)

## Context

Merchant API keys had no rate limit at the API. The only limiter was a per-process login limiter,
and the optional edge Worker's per-minute window. RelayPay has no Redis (ADR-003 and the scheduler
consolidation removed it), so a distributed limiter has to live in PostgreSQL, which every API
process already shares. The limiter sits in front of every payment command, so its cost and its
failure mode matter more than its precision.

## Decision

1. **GCRA, one statement.** The generic cell rate algorithm keeps one timestamp per key (the
   theoretical arrival time of the next request). Admission is a single
   `INSERT ... ON CONFLICT DO UPDATE ... WHERE ... RETURNING` on an autocommit connection: the
   check and the schedule advance are one atomic row operation, concurrent callers serialise on
   the row lock, a refused request changes nothing and so spends no budget, and a new key always
   inserts, so a key is never refused its first request.
2. **UNLOGGED table.** `api_rate_limit_state` is rewritten on every request and nothing depends on
   its durability. A crash resets every key's budget, which is the safe direction.
3. **Limits are per key.** `api_keys.rate_limit_rps` and `rate_limit_burst`, each falling back to
   `API_RATE_LIMIT_RPS` (100) and `API_RATE_LIMIT_BURST` (200). `burst` is the number of requests a
   fully rested key may make at once. Over-budget requests get `429 RATE_LIMITED` and a
   whole-second `Retry-After`.
4. **Count only what authenticated.** The limiter runs after API-key authentication, so a request
   with bad credentials cannot spend a real key's budget.
5. **Fail open.** If PostgreSQL errors, the request is served and
   `relaypay_api_rate_limit_decisions{outcome="failed_open"}` counts it. A best-effort protection
   must not become an outage of the payment API, and the payment path itself needs PostgreSQL
   anyway, so the window where the limiter is down and the API is up is short.
6. **The poller prunes** rows whose schedule lapsed an hour ago.

## Consequences

- Each API-key request costs one extra round trip to PostgreSQL. `api_rate_limit_state` has one
  row per active key, so the statement is a primary-key upsert (0.02 to 0.06 ms in the recorded
  plan, [money-path-plans.json](../evidence/v2.0.0/money-path-plans.json)).
- Hot keys contend on one row. That is the design: the contention is the limit.
- Session (admin) routes still use the per-process login limiter; this ADR covers API keys only.
- Fail open means an attacker who can make PostgreSQL error can lift the limit. That attacker
  already has a bigger lever (the database), so the trade is accepted.
