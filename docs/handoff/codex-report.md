# Codex handoff report

Updated: 2026-10-31. The continuation at the end of this report supersedes the earlier
not-started/blocker status. Execution initially stopped at C0's remote release gate, following
the handoff's scope boundary. The maintainer subsequently requested continuation;
the narrow historical-proof correction described below passed local verification and CI.

## Task results

| Task | Commit | Local gate | CI |
| --- | --- | --- | --- |
| C0: provider chaos, webhook push, local gate script | `302ffe6c54e07f6b3d0182e8d79c99ef336317f8` | `bash scripts/ci_local.sh`: ALL PASS | [Run 39810083150](https://github.com/marsh15/relaypay/actions/runs/39810083150): release-gate failure; proof-gate success; handwritten-gate failure as expected; chaos-full skipped |
| Historical proof follow-up | `aa91728c02b3060aff83958667e593d8bef12b95` | Full gate plus unchanged-code demo retry, detailed below | [Run 39826423138](https://github.com/marsh15/relaypay/actions/runs/39826423138): release-gate success, proof-gate success, handwritten-gate expected failure |
| C1: PSP adapters | Razorpay `9ed1a3e`; Cashfree/smoke `7b5cf577061776c03bed0a6537a1c58e81de00d2` | Full local gate ALL PASS | [Run 39831184327](https://github.com/marsh15/relaypay/actions/runs/39831184327): proof-gate success, release-gate failure in existing M13 test, handwritten-gate expected failure |
| C2: seller reconciliation CLI | `cd92c1e` | 127 non-proof tests; 2M-order memory proof passed | Current push tracked in Actions |
| C3: load-test kit | `9d5bc16` | Seven offline guards; four-VU HTTP run passed | Current push tracked in Actions |
| C4: money-path query plans | Not started | Not run | Not run |
| C5: admin resources and console pages | `210e07d` | 15 scoped backend tests; eight console journeys; contract/SDK checks passed | Current push tracked in Actions |
| C6: documentation catch-up | Current documentation/evidence commit | Source checks, local links and recorded evidence | Current push tracked in Actions |
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

## C1 adapter implementation and limits

Two Luna agents implemented the independent Razorpay and Cashfree adapters; a third
reviewed the result. They remain pure code with no inbox wiring or configuration
changes. Both normalize event outcomes per provider operation; a successful
authorization is not a capture. `EventOutcome` is reused from `psp/types.py`.

Focused tests cover raw-byte signature verification, replay windows, every mapped
event, malformed payloads, exact paise conversion, CSV errors with physical line
numbers, and numeric/timestamp boundary cases. A temporary inversion of the
Razorpay signature check caused its valid-signature test to fail, then the original
source was restored. The mutation log is `output/codex/c1-signature-mutation.log`.

Final focused coverage: 61 adapter tests passed (26 Razorpay, 35 Cashfree), four
offline smoke guards passed and the optional real sandbox test skipped. Repository
lint, formatting, and strict typing passed. The initial C1 gate was interrupted to
correct event timestamp semantics; `occurred_at` now consistently uses the signed
provider envelope timestamp, while entity timestamps are separate data.

The final full gate (`output/codex/c1-ci-local-final.log`) ended ALL PASS:
597 release tests, the demo, all isolated CI tests, deterministic evaluations,
9 proof tests, and six chaos seeds. Chaos ran 6,000 operations in 112.8 seconds
with 297 crashes, 456 faults, and zero harness failures. The invariant checker was
not evaluated. Summary: `output/chaos/summary-ci-20261022T182147Z.json`.

Known limits and interpretations:

- Wrong-secret and tampered-body failures both return `INVALID_WEBHOOK_SIGNATURE`:
  an HMAC mismatch cannot distinguish those causes. Missing headers, malformed
  timestamps, and stale timestamps have distinct codes.
- `parse_event(raw_body, headers=...)` optionally accepts headers because provider
  event identities are supplied there (`x-razorpay-event-id`, Cashfree's
  `x-idempotency-key`). Body-only parsing leaves `event_id` as `None`; it never
  invents a provider identifier. Future inbox wiring must retain the headers.
- Razorpay signs the raw body, without a delivery timestamp header. Its replay
  window uses signed envelope `created_at`; legitimate older retries will also
  fail that window. Cashfree signs the millisecond delivery timestamp plus body.
- Both settlement CSV headers are assumed normalized input schemas, explicitly
  documented in the fixture READMEs. Public material did not establish a stable
  downloadable export header schema. Real seller exports need maintainer
  confirmation before production use; no compatibility with an unseen CSV is
  claimed.
- Every fixture directory carries the required UNVERIFIED disclaimer. No real
  test-mode webhook payload was captured and no provider credentials were used.

Primary sources consulted:

- https://razorpay.com/docs/webhooks/validate-test/
- https://razorpay.com/docs/webhooks/payments/
- https://razorpay.com/docs/webhooks/refunds/
- https://razorpay.com/docs/payments/settlements/settlement-recon/
- https://www.cashfree.com/docs/payments/online/webhooks/signature-verification
- https://www.cashfree.com/docs/api-reference/payments/latest/payments/webhooks
- https://www.cashfree.com/docs/api-reference/payments/latest/refunds/webhooks
- https://www.cashfree.com/docs/api-reference/payments/latest/orders/get-order

`uv run python -m scripts.live_psp_smoke` makes GET requests only. It skips without
explicit `RAZORPAY_TEST_KEY_ID`/`RAZORPAY_TEST_KEY_SECRET`, or
`CASHFREE_TEST_CLIENT_ID`/`CASHFREE_TEST_CLIENT_SECRET` plus
`CASHFREE_TEST_ORDER_ID`. Razorpay keys must have the `rzp_test_` prefix; Cashfree
uses only the sandbox host. The command refuses live checks under `CI`. Its tests
carry `live_psp`; Make and CI marker expressions exclude them. The optional live
test skipped locally because keys were absent; four offline smoke guards passed.
These reads check credentials, not webhook fixture shapes.

## Measurements and remaining boundaries

### C1 remote gate blocker: M13 test crosses local midnight

On continuation on 2026-10-23, job-level inspection of run `39831184327`
showed `proof-gate=success`, `release-gate=failure`, and the expected
`handwritten-gate=failure`. Release job `120007426528` failed its Backend
correctness gate with 596 tests passing and this one failure:

```text
FAILED tests/integration/test_m13_settlement_intelligence.py::test_policies_forecasts_and_all_four_intents_end_to_end
tests/integration/test_m13_settlement_intelligence.py:395:
    assert numbers["refundCount"] == 1
E   assert 0 == 1
1 failed, 596 passed, 126 deselected
```

The test reads wall-clock time at line 297, records a refund at `now + 1 second`,
then asks for today's cash impact at `now + 1 minute` (line 367). The production
query correctly restricts refunds to the local calendar day containing that answer
moment (`packages/relaypay/settlement_intelligence/queries.py:450`). When `now`
falls in the final minute before midnight in Asia/Kolkata, the refund and question
land on different local days. This explains the CI execution around 18:30 UTC.

Verified without editing source files:

- The exact failed test passed unchanged at the current time: 1 passed in 6.86s
  (`output/codex/c1-m13-retry.log`).
- In an isolated Python process, replacing only the imported test module's clock
  with `2026-10-22T18:29:30Z` reproduced the exact assertion at line 395. The clock
  reference was restored in a `finally` block; no test, service, or DB clock was
  modified persistently.
- CI failure output is retained at `output/codex/c1-ci-failed.log`.

A separate authorized follow-up should give the existing M13 scenario a
deterministic time that keeps its refund and question on the intended local day,
while retaining the assertion. No test marker, threshold, assertion, or production
query was changed. Per the handoff's outside-task failure rule, C2–C7 remain
unstarted until this gate is resolved.

C2 RSS/time, C3 latency/RPS/machine information, and C4 plans/Seq Scans are
unavailable because those stages were not started. No performance conclusions or
index recommendations were fabricated.

Adapter inbox wiring, operation matching, and per-provider secret configuration remain
reserved follow-ups from the supplied brief.

All other explicitly excluded work remains untouched: UPI/requires_action,
merchant events v2, webhook-chaos harness integration, in-database reconciliation
scaling, GCRA, payout-locking wiring/benchmarks, migrations/index/hot-path changes,
cancel/refund-fee policy changes, fee-schedule writes, final release evidence and
the maintainer's handwritten modules. No additional out-of-scope implementation
defect was established during C0 review.

## Continuation: useful completion work, 2026-10-31

The maintainer requested broader completion work, direct commits to `main`, and Claude Haiku
5.5 high-effort subagents. They explicitly confirmed that all four maintainer-written module
groups must remain reserved. Those sources, their specification tests and `PROVENANCE.md`
are unchanged. The existing user edit in `scripts/portfolio_demo.py` and the untracked
`codex-handoff-prompt.md` were preserved.

Implemented and verified stages:

- `85b999e`: fixed the M13 scenario clock without changing a financial assertion or production
  query. Nine M13 integration tests and three clock tests passed. Its
  [CI run 41191117046](https://github.com/marsh15/relaypay/actions/runs/41191117046) completed
  `release-gate=success`, `proof-gate=success`, `handwritten-gate=failure` as expected.
- `4a22444`: repaired scheduler starvation discovered during documentation review. Previously
  every tick postponed every task, including skipped long-interval work. Deadlines now advance
  per attempted task; ordinary errors do not starve later tasks. Four regression tests fail
  against the old implementation (three failures, one crash test pass) and pass against the fix.
  No payment, finalizer, ledger or payout logic was changed.
- `cd92c1e`: added the stdlib-only offline seller CLI with streamed SQLite matching and reports,
  strict inputs, nine exception kinds, a sample with exactly one of each, and a memory proof.
- `9d5bc16`: added isolated TEST-tenant load setup, five k6 workloads, private temporary
  credentials, complete summaries on threshold failure, machine/dataset/commit capture and CI
  workload guards. Limiter `on/off` remains a label only; the documentation says so.
- `210e07d`: added scoped payment status filtering and journal/posting investigation reads,
  console list/detail pages and navigation. The additive admin contract and SDK were regenerated;
  frozen v1 checks pass. Browser testing caught and fixed the login allowlist and one ambiguous
  test locator. All eight console journeys passed with axe checks.
- Documentation now describes PSP signatures/inbox states, actual retry behavior, Redpanda's
  derived outbox role, dormant workflow boundaries, the four historical F1–F4 RCAs, ADR-012 and
  an honest M17 draft. Recovery review-budget changes are explicitly documented. No final v2
  release was written.

The complete measurements, commands and artifacts are in
[the evidence report](../evidence/2026-10-31.md). At implementation commit `210e07d`, the fresh
memory run processed 2,000,000 orders and 1,999,600 settlements in 72.138 seconds with
65,536,000 bytes peak RSS and the expected 2,400 findings. The clean four-VU, 30-second mixed
HTTP run completed 191 journeys/668 requests with p50/p95/p99 132.908/371.412/532.648 ms and
21.878 HTTP requests/s. The host was an eight-core Apple M1 with 8 GiB RAM; full context is
committed with the measurements. Earlier concurrent runs are distinguished in the report.

The original full local gate retains its `FAILURES` result: the broad suite collected one
reconciliation error-message parameter before its wording changed during implementation.
It recorded 720 passes and that one stale expectation. Current reconciliation and scheduler
tests passed together (131 tests), the ten property/chaos tests passed, all isolated workflow
commands and evaluations passed, and the six-seed/6,000-operation chaos sweep had zero harness
failures (291 crashes, 457 faults). The handwritten invariant checker was not evaluated.
Final lint, format, strict types, contract/SDK checks, console build/audit, edge and Compose
checks passed. Ignored logs remain in `output/completion-2026-10-31/`.

Independent Haiku reviews found no blockers. The journal-list scope/order query lacks a
measured index baseline; this is a C4/profiling follow-up, not a claimed performance defect or
an unreviewed index migration. No query-plan baseline or lock-contention measurement was
fabricated. C4 and optional C7 remain unselected; in-database reconciliation scaling, gateway
inbox wiring, provider replay chaos, rate limiting and final v2 release work remain deferred.
The handwritten checker/calculator/locking/ordering gates still require the maintainer.

All commits in this continuation land directly on `main`. Job-level results for the final push
are available from [relaypay-ci](https://github.com/marsh15/relaypay/actions/workflows/ci.yml).
This report is committed before those remote jobs finish; it makes no advance claim that they
passed. The overall workflow is expected to be red while `handwritten-gate` remains pending.

### Remote proof follow-up

Run [41209910236](https://github.com/marsh15/relaypay/actions/runs/41209910236) finished with
`release-gate=success`, `proof-gate=failure`, and the expected handwritten failure. The failed
proof measured inherited pre-exec pytest RSS on Linux (263,409,664 bytes), rather than the
standalone CLI's memory alone. The harness now starts its measured child from a lightweight
launcher. A Linux parent-memory regression and the full two-million-order proof passed in a
Linux container: 54,272,000 bytes peak RSS, 2,400 expected findings, unchanged 128 MiB bound.
The old measurement fails the new regression as intended. No row count, marker or threshold
was weakened.

Malformed UTF-8 mapping files also now return the documented input-error exit 2. Current
non-proof CLI tests: 128 passed; the extra Linux-only launcher guard passes on Linux and is
skipped on macOS. The follow-up push reruns the normal CI jobs; this note does not anticipate
their result.
