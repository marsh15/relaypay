# Settlement intelligence answers are deterministic with a bounded model role

Status: accepted for v0.14.0. A merchant settlement policy is an immutable, versioned record of one
IANA timezone, one daily cutoff (inclusive: a capture at exactly the cutoff belongs to that business
date), a T+0/T+1/T+2 weekday delay, and an include-or-skip weekend rule; public holidays are out of
scope. Forecasts are immutable pre-cutoff snapshots with deterministic line items; a re-forecast
writes a new sequence, never an update. Every number, calculation step, citation, and trace in an
answer comes from fixed tenant-scoped query functions in code. The model role is bounded to a typed
schema that can carry only an intent code and at most two dates, plus a narrative that is validated
after generation: altered or invented numeric values, unsupported claims, missing citations,
record references outside the deterministic result, and malformed output are rejected before
anything is persisted or returned. The cutoff instant itself is inclusive and arrival dates roll a
T+0 weekend settlement under skip semantics to Monday. This favors a reproducible, auditable answer
over a fluent one the model might have invented.
