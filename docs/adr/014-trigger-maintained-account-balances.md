# ADR-014: Merchant balances are trigger-maintained running totals of the ledger

Status: accepted (v2.0, after M19). Amends [ADR-004](004-immutable-ledger.md).

## Context

ADR-004 derived every balance from the immutable postings. `derive_balances` summed three
accounts per call, and the refund path summed AVAILABLE again. At one million postings, the sum for
an account holding 98% of them took 105 to 320 ms, depending on whether the planner picked an
index-only scan or a sequential scan ([evidence](../evidence/v2.0.0-m19-plans.md)). It grows
linearly, so a hot merchant paid about a second per payout, settlement or balance read. A typical
account took 2 ms.

The fix has to keep every ledger invariant. Postings and journals stay immutable and balanced by
deferred triggers (0001), and the payout-coverage constraint trigger (0020, serialised per merchant
by 0030) is the database's overdraft backstop. The chaos harness checks the money invariants on
every seed. A stored balance must be provably equal to the ledger, not a second authority.

## Decision

1. **One row per merchant-owned ledger account.** `ledger_account_balances(account_id,
   organisation_id, environment_id, credit_total, debit_total, posting_count)`, with a composite
   foreign key to `ledger_accounts`. The balance is `credit_total - debit_total`, signed by account
   type as before.
2. **The database maintains it in the posting's own statement.** `ledger_account_balances_open`
   (AFTER INSERT on `ledger_accounts`, merchant-owned rows only) creates a zero row with the account.
   `ledger_account_balances_apply` (a plain AFTER INSERT row trigger on `postings`, not deferred)
   adds each posting to its account's row. The cache and the posting therefore commit or roll back
   together, and no application code has to remember to update it. A posting to a merchant-owned
   account that has no row is refused (SQLSTATE 23514).
3. **Nothing else can write it.** The app role keeps only `SELECT`: the migration revokes INSERT,
   UPDATE, DELETE and TRUNCATE from every non-owner grantee, and the two maintenance functions are
   `SECURITY DEFINER` with `search_path` pinned to the migrated schema.
   `ledger_account_balances_guard` rejects any write not made from inside those triggers
   (`pg_trigger_depth() < 2`, SQLSTATE 55000), so the owner role is blocked too unless it
   disables the trigger. Other evidence tables rely on triggers alone (database.md). This table
   also revokes privileges, because a wrong total would be read as money.
4. **The ledger stays the truth, and the cache is checked against it.**
   `relaypay_ledger_balance_drift()` lists every account whose row disagrees with its postings,
   is missing, or should not exist. The Python wrapper is `ledger.service.balance_drift`, and
   `recompute_balance` is the per-account sum. Money invariant 16, `STORED_BALANCE_DRIFT`, checks
   the same thing on every chaos snapshot. The snapshot's merchant balances now come from the
   cache, so invariants 3 and 4 judge the values the services actually read.
5. **The backfill is one verified pass.** The migration locks `postings` and `ledger_accounts`
   against inserts for its own transaction, installs the triggers, and fills every row with a single
   `INSERT ... SELECT`. It then runs an independent check: the drift function must be empty, and the
   grand credit, debit, count and account totals must equal the merchant postings'. Otherwise it
   raises and nothing commits. The downgrade drops the table, triggers and functions; the cache is
   derived data, so nothing is lost.
6. **Platform accounts are not cached.** Every capture in an environment posts to provider
   clearing, and every fee capture posts to fee revenue and GST payable. A row lock there would put
   all captures in the environment in one queue. Capture and refund also post to clearing in
   opposite orders (clearing first in a capture, last in a refund), which is a deadlock pattern. No
   money path reads these balances. `credit_balance` falls back to `recompute_balance` for them.
7. **The overdraft guard keeps summing the ledger.** The 0020/0030 coverage trigger still sums
   AVAILABLE postings at commit instead of reading the cache. A defect in the cache then cannot
   cause an overdraft; the guard stays an independent check.

## Concurrency

The apply trigger's `UPDATE` holds the account row's lock until commit. Under READ COMMITTED a
waiting writer re-reads the committed row, so no increment is lost. RelayPay posts under READ
COMMITTED; the proof snapshot is READ ONLY. Deadlocks need two transactions to lock two cached
rows in opposite orders while neither holds a lock the other must wait for first:

- Settlement, refund, retry and pessimistic payout take `merchant_accounts ... FOR UPDATE`
  before posting.
- Captures and payout finalisations insert a journal first. Its foreign key takes `FOR KEY SHARE`
  on the same merchant row, which conflicts with `FOR UPDATE`. The merchant row therefore orders
  every merchant-scoped writer before any cached row is touched.
- Captures lock only PENDING. Payout finalisations lock AVAILABLE then PAYOUT_CLEARING, always in
  that order.
- The coverage trigger's advisory lock is taken only at commit, after the transaction has
  finished its postings.

The 60-way payout benchmark was correct for every strategy with 0 deadlocks. The new integration
test runs 8 threads × 25 journals on one account and loses no increment.

## Alternatives rejected

- **Checkpoint rows plus a bounded tail sum.** Postings have no commit-ordered sequence: ids are
  UUIDs, and `created_at` is the transaction start time. A checkpoint "through posting N" can
  miss a posting that took an earlier position but committed later. Making that correct needs
  snapshot-xmin bookkeeping and a background job, plus a new column on a 1M-row immutable table.
  It is more machinery, and a lag window, to avoid a lock this design accepts.
- **Append-only delta rows with periodic compaction.** This avoids writer contention, because each
  transaction inserts its own delta row. The read cost then depends on how far behind the compactor
  is, and the design adds a job whose failure turns into slow reads. It is the lever to pull if one
  merchant's posting rate becomes the bottleneck.
- **A deferred (commit-time) apply trigger.** It would shorten the lock window to the commit
  itself (about the 0 ms-hold figure below, whatever the transaction does). But a transaction that
  posts and then reads its own balance would see a total missing its own postings, so the cache
  would no longer equal the sum from every reader's point of view. Rejected for that reason.
- **Updating the cache from application code** (`post_journal`, or `merchant_accounts`, which
  0029 gave a `balance_version`). Every writer would have to cooperate, including raw SQL and
  future code. The trigger cannot be skipped. `balance_version` stays the optimistic-locking token
  for payout reservation only.
- **A materialised view.** Refreshing it is a full aggregate, and between refreshes it is stale.

## Consequences

- Balance reads are a primary-key lookup: 0.03 ms for a typical account and 0.15 ms for the hot
  one after `VACUUM`, at one million postings
  ([evidence](../evidence/v2.0.0-m19-plans.md)). Before, they took 2 ms and 105 to 320 ms.
- Concurrent postings to one merchant account serialise from the posting to the commit
  ([write-side evidence](../evidence/v2.0.0-m19-plans.md#what-it-costs-on-the-write-side)). In a
  synthetic capture-shaped transaction with 16 threads, throughput dropped from 1,099 to 590 per
  second with no work after the posting, and from 961 to 182 with 2 ms. Single-threaded and
  different-merchant throughput is unchanged. Moving a capture's posting later in its transaction
  would shorten the window. That change is not made here.
- Each posting to a merchant account adds one `UPDATE` of a small row (a HOT update; no indexed
  column changes). A posting to a platform account adds two primary-key probes.
- Anything that bulk-loads postings with `session_replication_role = replica` bypasses the
  triggers and must rebuild the affected rows. `scripts/capture_money_path_plans.py` does, and
  then asserts the drift function is empty for its tenant.
- `alembic check` covers the model; the upgrade proofs cover the migration. `verify_m18_upgrade`
  shows the backfill rebuilds exactly the rows the trigger maintained, equal to the ledger, and
  that 0035 and its downgrade change no other row.
