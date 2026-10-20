# ADR-011: A signed "not found" past a grace period is proof of absence

Status: accepted (v2.0, M16; amends ADR-002)

## Context

ADR-002 commits `SENT` before the mutation HTTP call and never resends afterwards. The M15
chaos harness showed the cost of one case it leaves open. If the process dies between
committing `SENT` and making the HTTP call, the provider (or bank) never receives the request.
Every later lookup answers "not found", which v1 classified as a non-business 4xx. After five
indeterminate lookups the operation went to `REQUIRES_REVIEW`, and `retry_lookup` could never
clear it. That was about 130 manual reviews per 20,000 chaos operations, for requests that
provably moved no money.

## Decision

A lookup answer is **verified not-found** only when all of the following hold:

- it is HTTP 404;
- it carries a valid signature from the provider's (or bank's) signing key;
- its body is exactly `{"accountId", "outcome": "NOT_FOUND", "stableKey"}`, bound to the
  account and stable key RelayPay asked about.

An unsigned or malformed 404 is still never evidence of absence. A signed not-found for a
different identity goes to review as mismatched evidence.

A verified not-found received at least `PROVIDER_NOT_FOUND_GRACE_SECONDS` (default 600) after
the recorded send (`last_sent_at`) is proof the effect never happened:

- the operation finalizes `FAILED` with failure code `PROVIDER_EFFECT_NOT_FOUND`, recorded as
  the attempt classification `VERIFIED_NOT_FOUND_FINAL`;
- a payout attempt fails with `BANK_EFFECT_NOT_FOUND`, and its reservation is released.

Within the grace period the operation keeps polling, never later than the deadline, and these
lookups do not count toward the five-lookup review limit. The finalizer accepts only
`VERIFIED_NOT_FOUND_FINAL`, never a within-grace not-found, as terminal evidence.

The grace period is the provider's lookup-consistency window. It must exceed the longest time
between a provider accepting a request and that request becoming visible to lookups, which the
2 s client timeout bounds for the simulators. A real PSP adapter must set it from that
provider's documented consistency guarantee.

## Consequences

- A request that never reached the provider ends in a definitive, automatic failure instead of
  a permanent human queue. The mutation is still sent at most once, and nothing is ever resent.
- An effect that exists always wins. A not-found can only follow from a provider that holds no
  effect for the stable key.
- RelayPay now trusts one more provider statement (signed absence), so a provider that loses
  effects or answers lookups inconsistently past the grace period would make RelayPay fail
  operations that actually succeeded. Reconciliation catches this as
  `MISSING_INTERNAL_TRANSACTION` against the provider statement.
- Operations already in `REQUIRES_REVIEW` from v1 clear on the next admin `retry_lookup` once
  the provider answers with a signed not-found.
