# RCA 005: An index that shared a foreign key's prefix made every existence check scan a tenant

Status: fixed before release, in migration 0033. Found by the M18 scale proof
([evidence](../evidence/v2.0.0-m18.md)). It never shipped.

## Symptom

Reconciling 20,000 lines took 56 to 117 seconds. Of that, inserting 20,000 rows into
`reconciliation_matches` took 47 seconds: 2.3 ms per row, against 0.65 ms in isolation.

## How it was found

`EXPLAIN (ANALYZE)` of the insert reports time per constraint trigger. One foreign key took 47 of the
48 seconds: `(organisation_id, environment_id, provider_operation_id)` to `provider_operations`.

## Root cause

The first draft of migration 0033 added `ix_provider_operations_scope_created` on
`(organisation_id, environment_id, created_at)` for the "operations missing from the statement"
query. Every foreign key into `provider_operations` checks
`WHERE organisation_id = $1 AND environment_id = $2 AND id = $3 FOR KEY SHARE`. The planner's generic
plan for that check preferred the new index, because its leading columns match and `id` becomes a
filter, and it then read every operation of the tenant (20,000 rows) to find one. The unique
index `(organisation_id, environment_id, id)` was right there and was not chosen.

## Fix

The index is now `(created_at, organisation_id, environment_id)`. `created_at` leads, so it cannot
compete with the foreign-key lookups, and the "missing from the statement" query still uses it for
its time window. The same insert dropped from 47 s to 2.8 s.

## Lesson

An index whose leading columns equal a foreign key's referenced columns can hurt every write that
checks that key, including writes in other tables. Put the range column first, or do not add it.
Check new indexes on a table that many foreign keys point at with a production-sized insert, not
with a read query alone.
