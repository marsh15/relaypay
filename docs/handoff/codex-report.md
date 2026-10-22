# Codex handoff report

Date: 2026-10-22. Execution initially stopped at C0's remote release gate, following
the handoff's scope boundary. The maintainer subsequently requested continuation;
the narrow historical-proof correction described below has been verified locally.

## Task results

| Task | Commit | Local gate | CI |
| --- | --- | --- | --- |
| C0: provider chaos, webhook push, local gate script | `302ffe6c54e07f6b3d0182e8d79c99ef336317f8` | `bash scripts/ci_local.sh`: ALL PASS | [Run 39810083150](https://github.com/marsh15/relaypay/actions/runs/39810083150): release-gate failure; proof-gate success; handwritten-gate failure as expected; chaos-full skipped |
| C1: PSP adapters | Not started | Not run | Not run |
| C2: seller reconciliation CLI | Not started | Not run | Not run |
| C3: load-test kit | Not started | Not run | Not run |
| C4: money-path query plans | Not started | Not run | Not run |
| C5: admin resources and console pages | Not started | Not run | Not run |
| C6: documentation catch-up | Not started | Not run | Not run |
| C7: optional fee-schedule reads | Not started | Not run | Not run |

C0 was reviewed read-only by a Luna subagent. The only implementation correction
made during verification was replacing a new lifespan `type: ignore` with an
`AsyncIterator[None]` annotation in `apps/provider/main.py`. The supplied local gate
was added as executable `scripts/ci_local.sh`. No handwritten or protected money-path
files were edited. The maintainer's `scripts/portfolio_demo.py` change and existing
untracked `docs/handoff/codex-handoff-prompt.md` were left untouched; `.claude/` was
not accessed or modified.

## Verification evidence

- Locked dependency sync and all five migration trees succeeded.
- Lint, format check, strict typing, frozen v1 compatibility, SDK drift, and the SDK
  example passed.
- M15, M16, and M17 upgrade proofs passed.
- Release suite: 536 passed, 121 deselected, in 633.00 seconds.
- Merchant-balance demo, all isolated pytest commands extracted from release-gate,
  and deterministic evaluations passed.
- Proof suite: 9 passed, 648 deselected, in 240.86 seconds.
- Local chaos: 6 seeds × 1,000 operations, 313.5 seconds, 291 injected crashes,
  460 faults, zero seeds with harness failures. The output explicitly states
  `INVARIANTS NOT EVALUATED: hand-written checker not implemented`; no money-invariant
  validation is claimed.

Local logs (ignored artifacts): `output/codex/c0-ci-local.log`,
`output/codex/c0-release-job.log`, and `output/codex/c0-m7-reproduction.log`.
Chaos summary: `output/chaos/summary-ci-20261022T155336Z.json`.

The existing local database contained approximately 16,700 merchant accounts;
unscoped snapshot tests accounted for much of the release-suite duration. No data
was deleted to accelerate the gate.

## Blocking failure and required follow-up

CI release job `119933674110` failed at “Prove v0.7 operations upgrade preserves
historical evidence”, command:

```text
uv run python -m scripts.verify_m7_upgrade
```

Relevant remote traceback:

```text
File "scripts/verify_m7_upgrade.py", line 92, in main
    _prove_provider()
File "scripts/verify_m7_upgrade.py", line 85, in _prove_provider
    assert _table_counts(database_url) == before
AssertionError
Process completed with exit code 1.
```

`scripts/verify_m7_upgrade.py:84` upgrades the provider schema from
`0002_provider_statements` to `head`. `_table_counts` at line 22 includes every table
except three explicitly excluded names. C0's migration adds `provider_chaos_profiles`
and `provider_webhook_outbox`, so the dictionary gains keys even when both tables
are empty. This is a historical-proof boundary problem, not evidence that old rows
changed. The RelayPay portion of the same script already stops at its historical
release boundary (`0012_observability`, line 50).

The script is outside C0's allowed edit list. No assertion, migration, or historical
proof was changed. A maintainer or separately authorized task must resolve the
historical provider proof, then rerun remote CI before C1 begins.
The same command was reproduced locally and exited 1 at the same assertion. The provided
`scripts/ci_local.sh` runs only M15–M17 upgrade proofs and therefore does not catch
this M7 failure; consider adding the earlier release upgrade proofs in that follow-up.

## Continuation: historical M7 proof correction

After the maintainer requested continuation, the provider upgrade target was pinned
to `0003_slow_fault`, the provider revision that shipped with M7. The count assertion
is retained unchanged. This follows the existing RelayPay-side historical boundary
and the rationale in commit `e71aadd`. A Luna subagent independently reviewed the
revision chain and confirmed the boundary. `scripts/ci_local.sh` now runs M7 too.

The corrected `uv run python -m scripts.verify_m7_upgrade` passed locally. The full
gate ran again (`output/codex/m7-fix-ci-local.log`): lint, format, typing, contracts,
SDK checks, M7/M15–M17 proofs, migrations, 536 release tests, isolated tests,
evaluations, 9 proof tests, and all six chaos seeds passed. Chaos exercised 6,000
operations with 296 crashes and 454 faults in 103.1 seconds; the handwritten
invariant checker was still not evaluated.

The script's final result was FAILURES because `make merchant-balance-demo` alone
hit a transient environment failure: `Failed to query Python interpreter`, with
`No such file or directory` at `/Users/santoshkumar/.cache/uv/.tmp4uV8Zr`.
The cache directory disappeared while verification was running. The exact failed
command was rerun without code changes and exited 0; its output is in
`output/codex/m7-fix-demo-retry.log`. Thus every gate command has a passing result,
but the original full-run log is retained with its failure rather than relabeled.

Starting dependencies initially exposed a full-disk error in Redpanda. Removing
770.1 MB of unused Docker build cache restored healthy startup; application data,
volumes, and source files were preserved.

## Measurements and remaining boundaries

C2 RSS/time, C3 latency/RPS/machine information, and C4 plans/Seq Scans are
unavailable because those stages were not started. No performance conclusions or
index recommendations were fabricated.

No Razorpay/Cashfree fixtures or adapters were created and no external documentation
was consulted, so there are no fixture-shape verification claims or source URLs yet.
Their inbox wiring, operation matching, and per-provider secret configuration remain
reserved follow-ups from the supplied brief.

All other explicitly excluded work remains untouched: UPI/requires_action,
merchant events v2, webhook-chaos harness integration, in-database reconciliation
scaling, GCRA, payout-locking wiring/benchmarks, migrations/index/hot-path changes,
cancel/refund-fee policy changes, fee-schedule writes, final release evidence and
the maintainer's handwritten modules. No additional out-of-scope implementation
defect was established during C0 review.
