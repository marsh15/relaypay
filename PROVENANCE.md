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

## Hand-written by the maintainer

The four modules below make the core money-safety decisions in v2.0. They are written by the
maintainer **with AI code generation and autocomplete turned off**. AI tooling produced only
each module's typed interface, its specification docstring, and the red tests written from that
specification. It never produced an implementation. Until a module is implemented it raises
`NotImplementedError`, and its tests (marker `handwritten`, `make handwritten`, CI job
`handwritten-gate`) fail.

| Module | Decides | Milestone | Status |
|---|---|---|---|
| [`packages/relaypay/proof/invariants.py`](packages/relaypay/proof/invariants.py) | Whether a system snapshot breaks any of the 15 money invariants | M15 | Pending |
| [`packages/relaypay/payouts/locking/pessimistic.py`](packages/relaypay/payouts/locking/pessimistic.py), [`optimistic.py`](packages/relaypay/payouts/locking/optimistic.py), [`constraint_guard.py`](packages/relaypay/payouts/locking/constraint_guard.py) | How concurrent payout reservations are serialised without overdraft | M15 stubs, M19 wired | Pending |
| `packages/relaypay/fees/calculator.py` | Integer-paise MDR, GST and refund fee reversal | M16 | Not yet scaffolded |
| `packages/relaypay/psp/ordering.py` | Applying duplicate and out-of-order PSP webhooks once, in a valid order | M17 | Not yet scaffolded |

When a module is implemented, its row changes to the commit that implemented it, and the CI
`handwritten-gate` job for that module must be green.

The supporting code is AI-assisted, not hand-written. That covers the snapshot reader
(`proof/snapshot.py`), the interfaces (`proof/snapshot_types.py`, `payouts/locking/types.py`),
the legacy control reserver (`payouts/locking/legacy.py`) and every test.

## Checking the claim

```bash
git log --follow --format='%h %an %ad %s' -- packages/relaypay/proof/invariants.py
```

The implementing commit for each module touches only that module (and this file's status
column). It contains no test changes, so the tests that judge it predate it.
