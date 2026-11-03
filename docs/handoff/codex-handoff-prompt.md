# Handoff prompt for Codex: RelayPay v2.0 (the well-bounded half)

Paste everything below the line into Codex, with the repository checked out at
`/Users/santoshkumar/myprojects/relaypay` (remote: https://github.com/marsh15/relaypay, branch `main`).

---

You are joining an in-flight project with no prior context. Read this whole brief before you
touch anything. It tells you what the project is, what has been built, the rules that cannot
bend, and the exact tasks that are yours. Other work is deliberately **not** yours. It belongs
to another engineer or to the maintainer. When a task drifts into that territory, stop and
record it in the handoff report (section 8). Do not do it.

## 1. What RelayPay is

RelayPay is an evidence-first, synthetic payment-orchestration backend: a portfolio-grade
system that behaves like a real Indian PSP integration (INR, integer paise), built so that it
can **prove** it never creates, loses or double-moves money. Version 1.0.1 shipped. The current
programme is **v2.0, "never loses a paisa"**, in milestones M15–M19.

- **Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 17 (the only datastore,
  no Redis or Celery), uv, ruff, mypy (strict), pytest plus Hypothesis, a Next.js 16 and React 19
  console (`apps/console`, with Playwright and axe), Docker Compose.
- **Processes** (`apps/`): `api` (merchant `/api/v1` and `/api/v2`, admin, and inbound
  webhooks), `worker/poller.py` (a broker-less poller), and simulators: `provider` (the mock
  PSP), `bank`, `commerce`, `receiver`, `dispute_network`, `communication_network` and
  `merchant_site`. `console` is the operator UI and `edge` is Caddy.
- **Library:** `packages/relaypay/` holds all domain code. `packages/relaypay_sdk/` is a
  generated client and must never be hand-edited.
- **Migrations:** five Alembic trees under `migrations/{relaypay,provider,receiver,bank,commerce}`.
- **Glossary:** `CONTEXT.md`. **Architecture:** `docs/architecture.md`, plus ADRs in
  `docs/adr/001…011`. **Plan of record:** `docs/plans/v2.0-never-loses-a-paisa.md`. Read it.
  Its migration-number map is out of date (see section 3).

### Core design ideas (do not "simplify" any of these)

1. **Send-before-HTTP, lookup-only recovery (ADR-002).** A provider operation is recorded
   before the HTTP call. After an ambiguous outcome (timeout or 5xx), RelayPay never resends.
   It only *looks up* by stable key. ADR-011 adds one rule: a signed "not found" received after
   a grace period proves absence.
2. **One finalizer.** `provider_operations/finalizer.py::finalize_verified_attempt` is the
   *only* code that moves money. A webhook, a lookup, a request and an admin action all reach it
   as recorded `provider_attempts` evidence.
3. **Immutable double-entry ledger.** Journals and postings are append-only. Balance is
   enforced by deferred constraint triggers, and balances are derived, never stored.
   Evidence tables have immutability triggers (from migrations 0001 and 0018).
4. **Persisted payment state machine.** `payments/transitions.py` is the single writer of
   `payment_intents.status`, and a DB trigger enforces the transition table.
5. **Idempotency.** Fingerprints are route-aware, replays are byte-exact, and reusing a key
   with a different body returns 409. Idempotency rows are never purged, because refunds
   deduplicate through them.
6. **Fees and GST (D7).** At capture, the platform fee plus 18% GST is posted, and the merchant
   is paid the net amount. Fee and GST are retained on refund.
7. **Postgres-only work queues.** Claims use `FOR UPDATE SKIP LOCKED` and leases, and every
   poller task contains per-item failures so that one bad item cannot stall a batch.

## 2. Settled decisions (do not relitigate)

| # | Decision |
|---|---|
| D1 | Stay in this repo as RelayPay v2.0. |
| D3 | Postgres only. Rate limiting will be GCRA in Postgres (not your task). |
| D4 | `PROVENANCE.md` lists **four modules the maintainer writes by hand**. Their stubs raise `NotImplementedError`, and their red tests carry the `handwritten` marker. |
| D5 | Razorpay and Cashfree adapters are built **fixtures-first** from public docs. Live smoke runs only with keys in `.env`, and never in CI. |
| D6 | A payment has one capture, of up to the authorised amount. |
| D7 | Fee plus 18% GST is posted at capture. The RETAIN policy applies on refund. |
| D10 | Load testing uses k6, via the `grafana/k6` image under a compose `load` profile. |
| D11 | CI chaos runs 20 seeds × 1,000 ops. The full run (200 × 10,000) happens on demand only. |
| D12 | The seller reconciliation CLI is a **stdlib-only**, standalone package with no network or DB access. |
| D13 | Deferred: Temporal, a Go dispatcher, an AI reconciliation assistant, multi-currency, and a public demo. |
| `/api/v1` | It is byte-frozen against `contracts/openapi/api-v1-v0.6.0.json`. New behaviour goes in `/api/v2`, and `contracts/openapi/v2.0.0.json` is current. |

## 3. Where the project stands (2026-10-22)

**Done and green in CI on `main`:**

- **M15 (proof harness):**
  - Shared test fixtures (`tests/support/`).
  - Hypothesis stateful tests.
  - 50-way HTTP duplicate tests and 500-way payout race tests.
  - The seeded chaos harness (`scripts/chaos/`, with crash points in `proof/crashpoints.py`).
  - Immutability migration 0018.
  - CI jobs: `release-gate`, `proof-gate`, `handwritten-gate` (red by design) and `chaos-full`
    (manual).
- **M16 (payment model):**
  - Fixes F1–F4: a refund could spend payout-reserved funds; payouts could be stuck in SENT;
    operations could be dispatched never; a signed not-found now finalizes (ADR-011).
  - The state machine (0019), the overdraft guard (0020) and the postings indexes (0021).
  - Partial capture (0022), authorization expiry (0023), void (0024 plus provider 0004), and
    fees and GST (0025).
  - `/api/v2`. Release notes are in `docs/releases/v2.0.0-m16.md`.
- **M17, so far:**
  - `32a533e`: merchant webhooks retry with jittered exponential backoff, up to 8 attempts.
  - `5792d2c`: a raw PSP webhook inbox, `POST /api/inbound/v1/psp/{provider}`. It verifies an
    HMAC over the timestamp and body, then stores each event once in `psp_events` (migration
    0026).
  - `8598872`: the PSP applier (`psp/applier.py`). Inbox events become WEBHOOK
    `provider_attempts` and are finalized through the one finalizer (migration 0027). Until the
    maintainer writes `psp/ordering.py::decide`, events stay `DEFERRED`.

**In the working tree, uncommitted and not yet verified** (written and unit and integration
tested, but the full CI-like run has not completed):

- Provider simulator chaos plus webhook push:
  - migration `migrations/provider/versions/0005_chaos_webhooks.py`;
  - `packages/relaypay/mock_provider/{chaos.py,webhooks.py}`, with edits to `models.py` and
    `service.py`;
  - `POST /control/chaos` and a lifespan pusher in `apps/provider/main.py`;
  - `PROVIDER_WEBHOOK_URL` and `PROVIDER_WEBHOOK_PUSH_SECONDS` in `config.py` and
    `compose.yaml`;
  - `tests/unit/test_provider_chaos.py` and `tests/integration/test_provider_chaos_webhooks.py`;
  - a docs line in `docs/api/reference.md`.

  **Task C0** verifies and commits it.
- `scripts/portfolio_demo.py`: **the maintainer's own uncommitted change. Never stage, edit,
  format or revert it.** Never touch `.claude/` either.

**Next migration numbers:** relaypay `0028`, provider `0006`, once 0005 is committed. None of
your tasks should need a migration. If one seems to, it is not your task (see section 8).

## 4. Non-negotiable rules

1. **Hand-written modules are off limits.** Never implement, edit, "fix" or delete these files
   or their spec tests:
   - `packages/relaypay/proof/invariants.py`;
   - `packages/relaypay/payouts/locking/{pessimistic,optimistic,constraint_guard}.py`;
   - `packages/relaypay/fees/calculator.py`;
   - `packages/relaypay/psp/ordering.py`;
   - `tests/unit/test_{invariants,payout_locking,fee_calculator,psp_ordering}_spec.py`.

   The `handwritten-gate` CI job is **meant to be red**. Never weaken markers, skip tests,
   lower thresholds, add `xfail`, or delete assertions to get to green.
2. **The money path is off limits.** Do not modify:
   - `payments/service.py` and `payments/transitions.py`;
   - `provider_operations/*`;
   - `ledger/*`;
   - `payouts/*`;
   - `merchant_balances/*`;
   - `event_delivery/*`;
   - `psp/inbox.py` and `psp/applier.py`;
   - `apps/worker/poller.py`;
   - `reconciliation/*`;
   - `identity/*`;
   - any existing migration.

   You may *read* all of these.
3. **API contract.** `/api/v1` must stay byte-identical, and every contract change must be
   additive. If you touch routes:
   1. Run `uv run python -m scripts.generate_openapi --baseline` and then
      `uv run python -m scripts.generate_openapi`.
   2. Confirm that the v1 paths and schemas are unchanged.
   3. Regenerate the SDK into `packages/relaypay_sdk/_generated` with `openapi-python-client`,
      using the config in `openapi-python-client.yml` and the input
      `contracts/openapi/v2.0.0.json`.
   4. Run `make api-contract sdk-drift`.
4. **Code quality.** Write code that reads like the surrounding code, with the same naming,
   docstring density and idioms. Specifically:
   - no `# type: ignore`;
   - no bare `object` types used to dodge typing;
   - no imports inside functions;
   - no `assert` in production code;
   - no `noqa` without a stated reason.

   `mypy` runs strict on `packages apps scripts`, and ruff on everything.
5. **Commits.** Work lands directly on `main`. Make one commit per verified stage, with a
   descriptive message (an imperative subject, then a body that says what and why), and push.
   Never commit unverified work, and never force-push.
6. **Verify like CI before every commit** (section 5). A green local full suite has twice been
   false confidence, because CI also runs isolated per-file pytest steps and post-suite demos.
7. **After every push, check job-level CI**, not the overall run result:
   `gh run view <id> --json jobs`. `release-gate` and `proof-gate` must be `success`, and
   `handwritten-gate` is `failure` by design. Sometimes the push event does not trigger a run.
   If none appears within about 5 minutes, start one with
   `gh workflow run relaypay-ci --ref main -f chaos_full=false`.
8. **Secrets.** Never put real keys in fixtures, tests or commits. The Razorpay and Cashfree
   fixtures use obviously fake secrets.

## 5. Local environment and the CI-like gate

- `docker compose up -d --wait postgres`. Postgres listens on `localhost:55432`, with dev roles
  defined in `infra/postgres/init/`. Run `uv sync --frozen`, then `make migrate`.
- Save this as `scripts/ci_local.sh` (your first commit may add it; it is not money-path code).
  Run it before every commit; it takes about 20 minutes:

```bash
#!/usr/bin/env bash
# Reproduce relaypay CI (release-gate + proof-gate essentials) locally, in CI order.
set -u
cd "$(dirname "$0")/.."
fail=0
step() { echo "== $1"; shift; if ! "$@"; then echo "!! FAILED: $*"; fail=1; fi; }
step lint make lint
step format make format-check
step types make typecheck
step contract make api-contract
step sdk make sdk-drift
step sdk-example make sdk-example
step migrate-check bash -c 'make migrate >/dev/null && for t in relaypay provider receiver bank commerce; do uv run alembic -c migrations/$t/alembic.ini check || exit 1; done'
for m in 15 16 17; do step "verify-m$m" uv run python -m "scripts.verify_m${m}_upgrade"; done
step release-tests make test PYTEST_MARK="not handwritten and not proof and not chaos"
step demo make merchant-balance-demo
uv run python - <<'PY' > /tmp/ci_isolated.sh
import re, yaml
wf = yaml.safe_load(open(".github/workflows/ci.yml"))
for st in wf["jobs"]["release-gate"]["steps"]:
    for line in re.sub(r"\\\n\s*", " ", st.get("run", "")).splitlines():
        line = line.strip()
        if line.startswith("uv run pytest"):
            print(re.sub(r"--junitxml=\S+", "", line) + " -q -p no:cacheprovider")
PY
while read -r cmd; do step "isolated: ${cmd:0:70}" bash -c "$cmd"; done < /tmp/ci_isolated.sh
step evaluations uv run python -m scripts.run_evaluations
step proof-tests uv run pytest -q -p no:cacheprovider -m "(proof or chaos) and not handwritten"
step chaos uv run python -m scripts.chaos.run --mode ci --seeds 6 --ops 1000
echo "== RESULT: $([ $fail = 0 ] && echo ALL PASS || echo FAILURES)"
exit $fail
```

- Console tasks also run `make console-check`, plus `make console-e2e` for any new journeys.
- Expected noise: the chaos run prints `INVARIANTS NOT EVALUATED: hand-written checker not
  implemented`. That is correct and is not a failure.

## 6. Your tasks, in order

Each task is one stage and one commit (two at most). Stop at the end of each task, run the
gate, commit, push and check CI, then start the next one.

### C0. Verify and commit the provider chaos and webhook-push stage

Do not redesign it. Run the gate. If something fails, fix it minimally inside the files listed
under "In the working tree" in section 3. Stage exactly those files, and never
`scripts/portfolio_demo.py`. Then commit, for example "Push signed webhooks and seeded chaos from
the provider simulator (M17)". The body should explain:

- each decision is drawn from `sha256(seed|stable key|aspect)`, and one-shot directives win;
- the outbox row is written in the effect's transaction;
- delivery is at-least-once, with drop, duplicate and delay as chaos aspects;
- the push is enabled only when `PROVIDER_WEBHOOK_URL` is set (compose sets it, production
  does not).

Also add `scripts/ci_local.sh`, in this commit or a separate one.

### C1. Razorpay and Cashfree adapters, fixtures-first (T17.4, pure code only)

- **Where:** `packages/relaypay/psp/adapters/{__init__,base,razorpay,cashfree}.py`,
  `tests/fixtures/psp/{razorpay,cashfree}/…`, `tests/unit/test_psp_adapters_*.py`, and
  `scripts/live_psp_smoke.py`.
- **`base.py`** is a small `Protocol` or ABC with these parts:
  - `verify(headers, raw_body, secret, now) -> None`, which raises `RelayPayError` with a 401
    and a specific code;
  - `parse_event(raw_body) -> ParsedPspEvent`, returning provider event id, event type,
    provider payment or refund id, amount in paise, currency, outcome in
    PENDING/SUCCEEDED/DECLINED, decline code, occurred-at, and the raw notes or metadata;
  - `parse_settlement_report(csv_bytes) -> list[SettlementRow]`.

  Reuse the shapes in `psp/types.py` where they fit. **Do not edit `psp/types.py` or
  `ordering.py`.**
- **Razorpay:** `X-Razorpay-Signature` is the hex HMAC-SHA256 of the raw body, keyed by the
  webhook secret. The event id is `x-razorpay-event-id`. Map these events:
  `payment.authorized`, `payment.captured`, `payment.failed`, `refund.processed`,
  `refund.failed`.
- **Cashfree (PG webhooks v2025-01-01 or the current docs version):** `x-webhook-signature` is
  the base64 HMAC-SHA256 of `x-webhook-timestamp + raw_body`. Map payment success, failed and
  user-dropped events, and refund status events.
- **Both providers:** enforce a replay window (default 300 s), and use constant-time compares.
- **Fixtures:** write realistic payloads from the providers' **public documentation**. Give each
  fixture directory a `README.md` that says "shapes from public docs, UNVERIFIED against real
  test-mode payloads; maintainer to confirm", and list the doc URLs you used.
- **Tests:** good signature passes; a tampered body, wrong secret, stale timestamp or missing
  header each fails with its own code; each mapped event type normalizes correctly;
  settlement CSVs parse (fees and tax in paise; reject malformed rows with line numbers).
- **`scripts/live_psp_smoke.py`:** it skips cleanly unless the provider test keys are present in
  the environment. Its tests use a new `live_psp` marker. Register the marker in
  `pyproject.toml`, and make sure CI never selects it: check every `-m` expression in `ci.yml`
  and the Makefile.
- **Not yours:** wiring adapters into the inbox route; mapping a provider payment id to a
  RelayPay operation; per-provider secrets in config. Record these as follow-ups.

### C2. Seller reconciliation CLI, a stdlib-only package (T18.3)

- **Where:** a new uv workspace member, `packages/relaypay_reconcile/`, with its own
  `pyproject.toml`, a console script `relaypay-reconcile`, a `src` or flat layout matching the
  repo's conventions, and tests under `packages/relaypay_reconcile/tests` or
  `tests/unit/reconcile/`. Add the workspace config to the root `pyproject.toml` and update
  `uv.lock` with `uv lock`. Keep the main package's dependencies unchanged.
- **Behaviour:**
  - `relaypay-reconcile --settlement s.csv --orders o.csv [--format razorpay|cashfree|generic] [--mapping m.json] [--out-dir DIR] [--report csv,json,md]`.
  - It streams both files into **sqlite3** temporary tables (`csv` module, chunked
    `executemany`) and joins in SQL. The whole file is never held in Python lists.
  - Exception classes: MISSING_IN_SETTLEMENT, MISSING_IN_ORDERS, AMOUNT_MISMATCH, DUPLICATE,
    FEE_DIFFERENCE, GST_DIFFERENCE, TIMING (settled outside a configurable window),
    REFUND_NOT_LANDED and FAILED_BUT_DEBITED.
  - Amounts are integer paise. Parse rupee strings exactly, with `Decimal`, never floats.
  - Deterministic output ordering.
  - The exit code is 0 for clean, 1 when exceptions are found, and 2 for an input error.
- **Constraints, enforced by tests:**
  - an AST test asserts that every import in the package is stdlib
    (`sys.stdlib_module_names`) or the package itself;
  - no sockets and no `relaypay` imports;
  - a peak-RSS test (marked `proof`, so it runs in `proof-gate`, not `release-gate`) generates a
    synthetic 2,000,000-row pair, runs the CLI in a subprocess, and asserts peak RSS stays under
    a stated bound (measure it, then set the bound with headroom, documented in a comment);
  - a sample seller export pair under `examples/reconcile/` with seeded exceptions of every
    class, plus a test asserting each seeded exception is found exactly once.
- **Docs:** add `packages/relaypay_reconcile/README.md`, covering usage, formats, the mapping
  JSON schema and the exception definitions.

### C3. Load-test kit (T19.2, measurement only)

- **Where:**
  - `loadtest/k6/{create_intent,authorize,capture,refund,mixed}.js`;
  - `compose.load.yaml`, with a `load` profile and a `k6` service using `grafana/k6`;
  - `scripts/loadtest_seed.py`, which creates an org, an environment, an API key and merchants
    via existing services or the admin API, exactly as `scripts/seed.py` does;
  - `scripts/machine_info.py`, which records CPU, RAM, OS, Postgres version and settings, and
    the git sha as JSON;
  - a `make load` target that writes `output/load/<timestamp>/{summary.json,machine.json}`.
- Use per-VU idempotency keys and realistic think time. Thresholds go in the scripts, but the
  run must still produce a summary when thresholds fail. Document how to run it, with the
  limiter "on" and "off" as a placeholder env var. The limiter itself is not yours.
- **Do not optimise anything.** If you see hot spots (for example `authenticate_api_key`
  updating `last_used_at` on every request in `identity/security.py`, or the per-create
  `ensure_default_merchant_account`), record the numbers in the report only.

### C4. Money-path query-plan capture (T19.4, read-only)

- **Where:** `scripts/capture_money_path_plans.py` and a `make plans` target. Model it on
  `scripts/capture_m7_query_plans.py`.
- It seeds about 1M postings, plus proportional payments, captures and refunds, into a
  **throwaway schema**, the way `scripts/verify_m2_upgrade.py` creates and drops one. It then
  runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on these:
  - the `derive_balances` SUM;
  - `_locked_payment`;
  - the refund reserved-sum and the trigger's per-payment sum;
  - the idempotency lookup;
  - each poller claim query;
  - the reconciliation match join;
  - the payments keyset list.

  Copy the SQL from the code, citing the source file and line in a comment.
- **Output:** `output/plans/<timestamp>.json` plus a Markdown summary flagging Seq Scans on big
  tables. Commit one baseline summary under `docs/evidence/v2.0.0-m19-plans-baseline.md`.
- **No index migrations.** Recommendations go in the report.

### C5. Admin list resources plus console pages (T16.5 leftovers and T19.5)

- **Backend:** in `packages/relaypay/operations/service.py`, add read-only keyset
  `ResourceQuery` entries for `payments` (filterable by status) and `journals`, with a
  journal-to-postings detail read. Follow the existing entries (`settlements`, `payouts` and so
  on) exactly, including tenant scoping by organisation and environment and the existing
  pagination contract. This file is **not** on the money-path list. These are reads only.
  Regenerate the contract and SDK if paths change, additive only.
- **Console:** `apps/console/app/payments/page.tsx` (a list with a status filter, linking to the
  existing `[paymentIntentId]` page) and `apps/console/app/ledger/**` (a journal list, then
  postings drill-down showing debit and credit balance per journal). Match the existing pages'
  data loading, error and loading states, auth, and styles (see `operations/page.tsx`).
- **Tests:**
  - backend integration tests for both resources, covering pagination, status filter and tenant
    isolation (another org's rows never appear);
  - Playwright journeys with axe checks for both pages, in the existing e2e setup;
  - `make console-check` passes.

### C6. Documentation catch-up for what has landed

- **`docs/api/reference.md`:**
  - the inbound PSP endpoint: its headers, the signature scheme, the replay window, and the
    responses (200 accepted or replayed, 401 codes, 404 `PSP_PROVIDER_UNSUPPORTED`,
    409 `PSP_EVENT_CONFLICT`);
  - the provider chaos control fields;
  - the webhook retry schedule.

  Take every fact from the code (`psp/inbox.py`, `apps/api/routes/inbound_psp.py`,
  `event_delivery/delivery.py`, `apps/provider/main.py`), never from memory.
- **`docs/configuration.md`:** `PROVIDER_WEBHOOK_SECRET` (required to be non-default in
  production), `PSP_WEBHOOK_REPLAY_SECONDS`, `PROVIDER_WEBHOOK_URL` and
  `PROVIDER_WEBHOOK_PUSH_SECONDS`.
- **`docs/adr/012-webhooks-are-evidence-not-a-money-path.md`:** webhooks become WEBHOOK
  attempts and go through the one finalizer; polling stays the guarantee; ordering is decided
  per operation by state; the decision is hand-written. Follow the ADR template in
  `docs/adr/README.md`, and add the ADR to its index.
- **RCAs** in `docs/rca/001-…004-….md` for F1–F4: the symptom, how the harness found it, the
  root cause, the fix commit (`419ab3f`, `a619b17`, `f3e7a13`), the regression test, and the
  lesson. Sources are `git show <sha>`, `docs/releases/v2.0.0-m15.md` and `v2.0.0-m16.md`.
- **`docs/operations/runbook.md`:** the PSP inbox statuses and what an operator does with
  REVIEW, UNMATCHED and DEAD_LETTER events; webhook dead letters; operations in
  REQUIRES_REVIEW; how to run `make chaos` and read a failing seed dump.
- **`docs/releases/v2.0.0-m17.md`:** a draft that covers only the commits that have actually
  landed, with an explicit "Not yet in M17" list taken from section 7.
- **`docs/limitations.md`:** add the M17 deferrals.

**Do not** write the final `docs/releases/v2.0.0.md`, README numbers or the blog. They need
evidence that does not exist yet.

### C7 (optional, only if C0–C6 are green). Fee-schedule admin read API

`GET` endpoints only: list a merchant's fee schedule versions, and get the current one. They
are read-only, admin-scoped, and use existing service and model code in `fees/models.py` and
`fees/service.py`, without touching `fees/calculator.py`. Creating versions is **not** yours.
The contract change must be additive, and the SDK must be regenerated.

## 7. Explicitly not yours (leave for the other engineer)

If you notice that any of these are needed, write it down. Do not start them.

- UPI collect / `requires_action`: `pending_deadline_at` in provider-operation recovery; UPI
  and card flavours in the simulator.
- Merchant events v2: relaxing `merchant_events` parents; `payout.succeeded.v1` and the void,
  expiry and cancel events.
- Feeding provider webhook chaos into the chaos harness, and the 3× webhook replay harness.
- Wiring the Razorpay and Cashfree adapters into the inbox (operation matching, per-provider
  secrets).
- M18 in-DB reconciliation at scale: COPY import, chunked matching, statement fee and tax
  columns, daily auto-import, and the console upload endpoint.
- GCRA rate limiting. Payout-locking wiring, `balance_version` and the locking benchmark.
- Any migration, index or hot-path fix arising from C3 and C4 findings.
- Cancel before authorization; refund fee reversal; creating fee schedule versions.
- The final v2.0 release: evidence JSON, README numbers and drift check, release notes, the
  blog, and the full chaos run.
- The four hand-written modules (the maintainer's).

## 8. When you finish, or get blocked

Write `docs/handoff/codex-report.md` and commit it with your last task. Include:

1. Each task, with its commit sha(s), the gate result and the CI run id with job conclusions.
2. Every place a task needed something on the not-yours lists, with file:line and why.
3. Measurements from C2 (RSS and time), C3 (p50/p95/p99 and RPS, with machine info) and C4
   (Seq Scans found).
4. Any doubts about the Razorpay and Cashfree fixture shapes, with the doc URLs used.
5. Anything in the existing code that looked wrong. **Report it; do not fix it** if it is on a
   not-yours list.

If the gate fails for a reason outside your task's files, stop. Do not "fix" money-path code.
Record the failing command and output in the report.
