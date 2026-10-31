# RCA 004: A never-sent operation was parked in review forever (F4)

Status: fixed in M16. Sources: `git show f3e7a13`, [ADR-011](../adr/011-signed-not-found-is-proof-of-absence.md),
[docs/releases/v2.0.0-m15.md](../releases/v2.0.0-m15.md), and
[docs/releases/v2.0.0-m16.md](../releases/v2.0.0-m16.md).

## Symptom

A crash lands between committing `SENT` and making the HTTP call. The provider or bank never received
the request, so every later lookup answers "not found". Version 1 treated that answer as indeterminate.
After five lookups the operation went to `REQUIRES_REVIEW`, and `retry_lookup` could never clear it.
Payouts had the same problem: `create_retry` accepted only FAILED, so the payout could not be retried.

## How it was found

The M15 chaos harness. It produced this state 130 times in 20 seeds of 1,000 operations, as recorded
in the M15 release table. The harness reported it as a finding until the fix landed. The requests had
moved no money, but each one needed a person.

## Root cause

Version 1 classified every 404 as indeterminate. Absence was never treated as evidence, even though a
signed, identity-bound not-found is a provider statement.

## Fix

`f3e7a13` implements [ADR-011](../adr/011-signed-not-found-is-proof-of-absence.md):

- A lookup is a verified not-found only when it is HTTP 404, carries a valid signature from the
  provider's or bank's signing key, and has the exact body
  `{"accountId", "outcome": "NOT_FOUND", "stableKey"}`, bound to the identity RelayPay asked about.
  An unsigned or malformed 404 is still never evidence. A signed not-found for a different identity
  goes to review.
- A verified not-found received at least `PROVIDER_NOT_FOUND_GRACE_SECONDS` after the recorded send
  (default 600) finalizes the operation FAILED with `PROVIDER_EFFECT_NOT_FOUND`. A payout attempt
  fails with `BANK_EFFECT_NOT_FOUND` and releases its reservation.
- Within the grace period, the operation keeps polling and does not count toward the five-lookup
  limit. The finalizer accepts only the final classification as terminal evidence.

## Regression tests

- `tests/integration/test_f4_signed_not_found.py`: within-grace polling past the review limit,
  after-grace failure, the admin backlog clearing on the next lookup, and a refund reservation being
  freed. An effect that exists always wins.
- `tests/unit/test_provider_not_found_classification.py`: classification of signed, unsigned,
  foreign-key, mismatched, and extra-field bodies.
- Per the commit message, disabling the grace branches turns four tests red.

## Lesson

Absence counts as evidence only with a valid signature, an identity match, and a grace period tied to
the provider's lookup-consistency window. Without all three it must stay indeterminate. The grace
period has to be set from the real provider's documented consistency guarantee, not the simulator's.
