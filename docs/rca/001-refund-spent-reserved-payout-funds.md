# RCA 001: A refund could spend funds reserved for a payout (F1)

Status: fixed in M16. Sources: `git show 9952e77 419ab3f 5cd01a4`,
[docs/releases/v2.0.0-m15.md](../releases/v2.0.0-m15.md), and
[docs/releases/v2.0.0-m16.md](../releases/v2.0.0-m16.md).

## Symptom

A merchant settles 100000 paise, reserves 100000 paise for a payout, and then refunds the capture in
full. When the payout finalizes, the merchant's AVAILABLE balance is −100000 paise. The customer was
refunded and the merchant was paid the same money.

## How it was found

The M15 proof work recorded the correct behaviour as a strict xfail,
`tests/integration/test_f1_refund_vs_reserved_payout.py` (commit `9952e77`). The derived balance hid
the fault: `derive_balances` clamped `payout_eligible` at 0, so the stored postings were never
checked against a negative total.

## Root cause

`_refund_sources` in `packages/relaypay/ledger/service.py` drew on raw AVAILABLE_PAYABLE postings.
It ignored live payout reservations. The read also was not taken under the merchant-account lock
that the refund path holds, so a concurrent reservation could change the answer.

## Fix

- `419ab3f`: a refund draws on AVAILABLE only up to the unreserved part (available minus reserved).
  The remainder is booked to MERCHANT_RECEIVABLE. The read happens under the merchant-account row
  lock that `post_refund_journal` takes. The reservation sum is factored into
  `reserved_payout_amount()`, which both `derive_balances` and the refund path use.
- `5cd01a4` (migration 0020): a deferred constraint trigger, `relaypay_validate_payout_coverage`,
  checks at commit that the payout reservations for a merchant never exceed its AVAILABLE credits
  minus debits. This is a database backstop, so a future wrong check in code cannot overdraw.

## Regression tests

- `tests/integration/test_f1_refund_vs_reserved_payout.py`: the strict xfail is now an ordinary
  passing test.
- `tests/integration/test_m4_payouts.py`: a partial reservation (60000 of 100000) splits the refund
  40000 from AVAILABLE and 60000 to receivable, with matching balance-transaction deltas.
- `tests/integration/test_payout_overdraft_guard.py`: raw reservation and raw AVAILABLE-debit writes
  that would overdraw are rejected at commit; exact coverage is allowed.

## Lesson

A derived view that clamps can hide a negative raw balance. Check invariants against the raw
postings, and make the database refuse what the code must never do. As `5cd01a4` puts it, one wrong
check is enough.
