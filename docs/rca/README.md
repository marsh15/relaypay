# Root-cause analyses

Each record covers one defect the v2.0 proof harness found: the symptom, how it was found, the root
cause, the fix commit, the regression test, and the lesson. The sources are `git show <sha>` and the
release notes. Records are written once, after the fix lands, and are not edited afterwards.

- [RCA 001: A refund could spend funds reserved for a payout (F1)](001-refund-spent-reserved-payout-funds.md)
- [RCA 002: A payout stayed SENT forever after the bank had paid (F2)](002-sent-payout-stuck-after-bank-paid.md)
- [RCA 003: An operation committed but never dispatched was never recovered (F3)](003-undispatched-operation-never-recovered.md)
- [RCA 004: A never-sent operation was parked in review forever (F4)](004-signed-not-found-parks-never-sent-operations.md)
