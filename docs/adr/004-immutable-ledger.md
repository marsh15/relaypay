# ADR-004: Immutable double-entry ledger

Status: accepted

## Decision

Successful capture/refund finalization posts a journal with at least two INR postings. Deferred
database constraints require equal debit and credit totals, and triggers reject updates/deletes of
posted history. Balances are derived from postings rather than stored counters.

## Consequences

Application defects cannot commit unbalanced or mutable financial history. Authorization creates
no journal. Corrections require new compensating domain entries, and reporting must aggregate the
append-only posting stream.

Amended by [ADR-014](014-trigger-maintained-account-balances.md): merchant balances are now read
from running totals that a posting trigger keeps in the posting's own transaction. The postings
remain the source of truth; the totals are a cache, checked against them by a money invariant.
