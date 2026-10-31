# RCA 003: An operation committed but never dispatched was never recovered (F3)

Status: fixed in M16. Sources: `git show a619b17`,
[docs/releases/v2.0.0-m15.md](../releases/v2.0.0-m15.md), and
[docs/releases/v2.0.0-m16.md](../releases/v2.0.0-m16.md).

## Symptom

The API commits a provider operation, then the process dies before its inline dispatch to the
provider. The operation was never sent. Nothing recovered it unless the client retried with the same
Idempotency-Key.

## How it was found

The M15 chaos harness. It produced this state 104 times in 20 seeds of 1,000 operations, as recorded
in the M15 release table. The harness reported it as a finding until the fix landed.

## Root cause

`claim_due_operations` required `last_sent_at`. A recovery path that requires the evidence the crash
prevented from existing can never fire for a crash before the send.

## Fix

`a619b17`:

- `claim_unsent_operations()` leases PROCESSING operations that are still unsent 30 seconds after
  creation.
- `dispatch_unsent_operations()` first-sends them through `dispatch_operation`. `run_recovery_batch`
  runs it first.
- `prepare_first_send` re-locks the row. A caller that finds `last_sent_at` already set becomes a
  lookup. A late inline dispatch from the API therefore races the poller and still yields exactly
  one mutation.

## Regression tests

- `tests/integration/test_f3_undispatched_operation_recovery.py`: reproduces a never-dispatched
  initiate, then asserts convergence and exactly one external effect. It also races recovery against
  a slow live dispatcher, the late inline dispatch, and two concurrent sweeps.
- Per the commit message, removing the fix turns the F3 tests red.

## Lesson

A recovery path must not require the evidence that the failure it recovers from prevents. The first
send and a late inline send must converge on one mutation, and the way to get that is a row lock and
a re-check under the lock.
