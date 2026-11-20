# RCA 006: Freshly imported statement rows had no statistics

Status: fixed before release, in `reconciliation/scale.py`. Found by the M18 scale proof
([evidence](../evidence/v2.0.0-m18.md)). It never shipped.

## Symptom

The query that finds operations missing from a statement took 34 seconds for 20,000 rows, although
the same query run by hand a minute later took milliseconds.

## How it was found

`EXPLAIN (ANALYZE)` with the application's bound parameters showed
`Rows Removed by Join Filter: 199433637`: a nested-loop anti join comparing 20,000 operations with
20,000 statement rows pairwise, because the planner expected `statement_items` to contain one row.

## Root cause

A streamed import inserts the whole statement in one transaction. Until autovacuum analyzes the
table, `pg_stat_user_tables` shows `n_live_tup = 0` and the planner has no distribution for it. The
application role cannot `ANALYZE` a table it does not own, so the import could not fix that itself.
By the time anyone looked, autovacuum had run, which is why the hand-run query was fast.

## Fix

Algorithm 2 never joins against `statement_items` for the big comparison. Each chunk, and the
statement's key list, is first copied into a temporary table owned by the session, which the session
may `ANALYZE`; the planner then has real counts for the join to `provider_operations`.

## Lesson

A bulk load followed immediately by a join is the case where statistics are stalest. If you cannot
analyze the table you loaded, stage what you need to join in something you can.
