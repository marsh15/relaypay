# RCA 002: A payout stayed SENT forever after the bank had paid (F2)

Status: fixed in M16. Sources: `git show a619b17`,
[docs/releases/v2.0.0-m15.md](../releases/v2.0.0-m15.md), and
[docs/releases/v2.0.0-m16.md](../releases/v2.0.0-m16.md).

## Symptom

A worker dies after the bank has paid but before it records the response. The payout stays
PROCESSING, its attempt stays SENT, and its funds stay reserved indefinitely.

## How it was found

The M15 chaos harness, with crash points between poller tasks and around payout bank sends. It
produced this state 30 times in 20 seeds of 1,000 operations, as recorded in the M15 release table.
The harness reported it as a finding until the fix landed.

## Root cause

`run_payout_batch` selected only PREPARED and AMBIGUOUS attempts. No selector matched SENT, so a
SENT attempt with no response was never touched again.

## Fix

`a619b17`:

- `claim_stale_sent_attempts()` leases a SENT attempt older than a 30-second grace period. It uses
  `updated_at` as the lease, under `FOR UPDATE SKIP LOCKED`, and the batch then re-dispatches it.
- Because `prepare_attempt_send` sees `last_sent_at`, recovery is a bank lookup only. The transfer
  is never sent again.

## Regression tests

- `tests/integration/test_f2_payout_sent_recovery.py`: reproduces the gap with crash points, then
  asserts convergence and exactly one external effect. It also races recovery against a slow live
  dispatcher and against two concurrent sweeps.
- Per the commit message, removing the fix turns the F2 tests red.

## Lesson

A recovery query must select every state the worker can leave behind. A state that no selector
matches is a stuck state, and the chaos harness is the way to find it. Recovery of an ambiguous send
is lookup-only, which is what keeps the recovery from moving money twice.
