# Provenance

How RelayPay's code was written, stated plainly so nobody has to guess.

## AI-assisted by default

Most of this repository was written with AI coding assistants, mainly OpenAI Codex (the
`codex/*` branches) and Anthropic Claude, then reviewed, run and corrected by the maintainer.
That includes the v0.1–v1.0.1 history and the v2.0 harness scaffolding, tests and documentation.
The correctness claims in this repository don't depend on who typed the code. They rest on
evidence anyone can re-run: the test suite, the upgrade proofs, the Hypothesis state machines,
the contention tests and the chaos sweep (see [docs/test-evidence.md](docs/test-evidence.md) and
[docs/evidence/](docs/evidence/)).

## The four core decision modules

These four modules make the core money-safety decisions in v2.0. They were **originally reserved
for the maintainer to write by hand** with AI code generation turned off: AI tooling produced only
each module's typed interface, its specification docstring, and the red tests written from that
specification.

On 2026-11-04 the maintainer asked Claude (Anthropic) to implement all four so the project could be
completed. They are therefore **AI-written, not hand-written**. The specification tests predate the
implementations and were not changed to fit them, and the maintainer has not yet reviewed the code
line by line. The `handwritten` pytest marker and `handwritten-gate` CI job keep their names because
they select those specification tests; they no longer imply human authorship.

| Module | Decides | Milestone | Implemented by | Commit |
|---|---|---|---|---|
| [`packages/relaypay/proof/invariants.py`](packages/relaypay/proof/invariants.py) | Whether a system snapshot breaks any of the 15 money invariants | M15 | Claude, at the maintainer's request | `7b591de` |
| [`packages/relaypay/payouts/locking/pessimistic.py`](packages/relaypay/payouts/locking/pessimistic.py), [`optimistic.py`](packages/relaypay/payouts/locking/optimistic.py), [`constraint_guard.py`](packages/relaypay/payouts/locking/constraint_guard.py) | How concurrent payout reservations are serialised without overdraft | M15 stubs, M19 wired | Claude, at the maintainer's request | `50b8c08` |
| [`packages/relaypay/fees/calculator.py`](packages/relaypay/fees/calculator.py) | Integer-paise MDR fee, GST on the fee, merchant net (`compute_capture_fee`) | M16 | Claude, at the maintainer's request | `b213fd0` |
| [`packages/relaypay/psp/ordering.py`](packages/relaypay/psp/ordering.py) | What one verified PSP webhook means for its operation: apply, ignore as duplicate or stale, or review (`decide`) | M17 | Claude, at the maintainer's request | `b213fd0` |

Running the invariant checker on real chaos data surfaced a double-finalization defect in the
finalizer (`66f8a36`), and the 80-way payout race showed the constraint-guard strategy overdrawing
until the coverage trigger took a per-merchant lock (`50b8c08`, migration 0030). Both are fixed
with regression evidence.

The supporting code is AI-assisted, not hand-written. That covers the snapshot reader
(`proof/snapshot.py`), the interfaces (`proof/snapshot_types.py`, `payouts/locking/types.py`),
the legacy control reserver (`payouts/locking/legacy.py`), the fee types and service
(`fees/types.py`, `fees/service.py`), the PSP inbox, event types and applier (`psp/inbox.py`,
`psp/types.py`, `psp/applier.py`), and every test. Ledger tests use a constant fee test
double, so they never encode the calculator's rules.

## Checking the claim

```bash
git log --follow --format='%h %an %ad %s' -- packages/relaypay/proof/invariants.py
```

The implementing commits listed above contain no changes to the specification tests, so the tests
that judge each module predate it.
