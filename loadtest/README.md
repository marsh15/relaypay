# Synthetic payment load measurements

Start the local stack with `docker compose up -d --build --wait`, install with
`uv sync --frozen`, then run from the repository root:

```bash
make load
uv run python -m scripts.run_loadtest --script capture --vus 8 --duration 60s
uv run python -m scripts.run_loadtest --script refund --vus 4 --duration 30s
```

The command creates an isolated TEST organisation, merchant, customer and scoped API key.
It does not reuse the demo tenants. The plaintext key is mounted from a mode-0600 temporary
file and removed after the run; artifacts contain public tenant ids only. The key remains
active in the local database. Reset the disposable sandbox when finished (see the reset
runbook); seeding is refused in production mode. Never point this kit at real providers.

`--script` supports `create_intent`, `authorize`, `capture`, `refund`, and `mixed`.
Every iteration creates a fresh payment. Authorize, capture and refund workloads include
their prerequisite API calls; `command_latency` tagged by command separates endpoint costs.
`mixed` alternates captured-only and fully-refunded payments. Keys contain the run, virtual
user, iteration and command, so parallel users and separate runs cannot reuse a key.
Each journey pauses 100 ms. An unexpected or unresolved operation fails the journey and
stops its remaining mutations; it is never blindly resent. Use fault/proof tests to verify
recovery rather than enabling provider chaos during a clean latency baseline.

Artifacts are under `output/load/<UTC timestamp>/`:

- `summary.json`: complete k6 aggregate metrics, p50 (`med`), p95, p99, throughput,
  error/check rates, journey success, workload and limiter labels.
- `machine.json`: host CPU/RAM/OS, PostgreSQL version/settings, initial dataset counts,
  git commit/dirty flag and the Compose-default process/pool topology. Docker VM resources
  may differ from the host; record overrides and VM limits alongside published evidence.
- `tenant.json`: public ids to investigate this run in the database.
- `exit.json`: k6/container exit status. Threshold failure returns nonzero and still writes
  the summary; a startup failure can prevent a summary, so inspect this status first.

Thresholds (`<1%` HTTP errors, `>99%` journey success, command p95 `<2s`) are local
regression tripwires, not service guarantees. k6's `handleSummary` persists results even
when these thresholds fail. See the [official summary documentation](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/).

`--limiter on|off` is a **label only** for the planned rate limiter. No API rate limiter is
implemented by this kit; both labels currently run identical server behavior. Do not publish
an on/off performance comparison as if traffic were actually limited.

This kit measures HTTP behavior. It does not measure duplicate financial effects, recovery
latency, lock contention, queue lag or container resource consumption. Those require the
proof suite and separate operational measurements. Do not equate a successful k6 run with
validated financial invariants; the maintainer's invariant checker is still pending.

Run the offline workload guard tests with:

```bash
node --experimental-vm-modules --test loadtest/k6/common.test.mjs
```
