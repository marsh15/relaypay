# Operations runbook

This runbook covers the sandbox as built at M17. It describes what the code does and which
endpoints and commands an operator can use. Data is synthetic. Nothing here is a production
procedure, and no metric, SLO or latency target is stated because none is measured here.

Read operation state from the evidence endpoint, `GET /api/v1/payment_intents/{id}/evidence`
(session), which lists provider operations, attempts, and operation history. Route references are in
[the API reference](../api/reference.md).

## PSP inbox

Inbound PSP webhooks are stored in `psp_events` (migration 0026) and applied by the poller task
`apply_psp_events`, on a one-second minimum cadence, up to 50 events per tick. No HTTP endpoint or admin screen reads
`psp_events` at this checkout, so inspect it with read-only SQL against the `relaypay` database.

| Status | Set when | Outcome code | What an operator does |
| --- | --- | --- | --- |
| `PENDING` | Accepted and matched to one operation | none | Nothing. The poller leases it on the next tick. A long-lived `PENDING` row means the poller is not running. |
| `PROCESSING` | Leased by the poller | none | Nothing. A lease lapses after 30 seconds and the event is re-leased. |
| `APPLIED` | Recorded as a `WEBHOOK` provider attempt and finalized | none | Nothing. |
| `IGNORED` | The ordering decision says duplicate or stale | `DUPLICATE` or `STALE` | Nothing. A duplicate or stale report changes no operation. |
| `DEFERRED` | Matched, but the ordering decision was unavailable | `ORDERING_DECISION_UNAVAILABLE` | Nothing. The operation is resolved by polling. The applier does not re-lease `DEFERRED` rows, so they are not retried later by the poller. `decide` is implemented, so this status is not produced in normal operation. |
| `REVIEW` | The envelope is invalid, or its identity does not match the operation | `INVALID_EVIDENCE` or `MISMATCHED_EVIDENCE` | Investigate, as described under [REQUIRES_REVIEW](#operations-in-requires_review). The operation is also moved to `REQUIRES_REVIEW` with the same reason. |
| `UNMATCHED` | No operation has that stable key at ingest, or more than one does | none | Nothing automatic. The applier never claims or re-matches `UNMATCHED` rows. Keep the row for reconciliation, and find out which provider effect it describes. |
| `DEAD_LETTER` | Claimed five times without finishing | `ATTEMPTS_EXHAUSTED` | Find the cause in the logs (`psp_event_apply_failed`, with the event id). Repeated finalize failures move the operation to `REQUIRES_REVIEW` with reason `APPLY_FAILURE` after three attempts. |

The `REVIEW`, `UNMATCHED`, and `DEAD_LETTER` rows are never deleted. The raw bytes and their digest
are frozen by the immutability trigger from migration 0018.

## Operations in REQUIRES_REVIEW

An operation moves to `REQUIRES_REVIEW` with one of these reasons:

- `PROVIDER_INDETERMINATE`: five lookups have produced no verified answer. Webhook evidence does
  not count toward this limit.
- `APPLY_FAILURE`: three finalize attempts for a PSP webhook failed.
- `INVALID_EVIDENCE` or `MISMATCHED_EVIDENCE`: a PSP webhook was invalid or named a different
  identity.
- A signed "not found" for a different identity, from the provider or bank. It goes to review rather
  than counting as absence ([ADR-011](../adr/011-signed-not-found-is-proof-of-absence.md)).

Procedure:

1. Read the operation and its history with the evidence endpoint.
2. Run one provider lookup with `POST /api/v1/operations/{operation_id}/retry_lookup` (session,
   CSRF, and organisation admin). The request needs the operation to be in review, and it needs a
   recorded send. The lookup never resends the mutation. Otherwise the endpoint returns
   `409 OPERATION_NOT_IN_REVIEW` or `409 LOOKUP_NOT_AVAILABLE`.
3. The operation changes state only through the finalizer, and only on a verified provider answer.
   If the answer is still unverified, it stays in review.

This runbook does not describe a manual override, and the API reference documents none for
operations. Do not edit rows to clear a review.

## Merchant webhook dead letters

Read a delivery and its attempts with `GET /api/v1/webhook_deliveries/{id}` (session). A delivery
goes to `DEAD_LETTER` for one of these reasons:

- a permanent status (any non-`2xx` that is not `408`, `429`, or `5xx`, including `3xx`);
- eight attempts without a `2xx`;
- an unreadable endpoint secret (`WEBHOOK_SECRET_UNREADABLE`), which dead-letters immediately;
- a destination other than `{RECEIVER_BASE_URL}/webhooks/relaypay`. The transport refuses it, the
  refusal is recorded as `WEBHOOK_TRANSPORT_ERROR`, and it is retried first.

After the cause is fixed, `POST /api/v1/webhook_deliveries/{id}/replay` (session, CSRF, and
organisation admin) creates a new delivery of the same stored event bytes. A replay does not change
those bytes.

## Provider push dead letters (simulator only)

The provider simulator pushes webhooks only when `PROVIDER_WEBHOOK_URL` is set. Compose sets it;
`compose.production.yaml` does not, so production containers push nothing. A failed push is retried
at `min(2^n, 60)` seconds, and after eight failed sends its row in the provider database's
`provider_webhook_outbox` table becomes `DEAD`. There is no endpoint for it. The operation still
resolves through lookups, because polling remains the guarantee.

## Redpanda and the outbox

Redpanda is a startup dependency of the poller, and the outbox publish step uses it on every tick:

- Compose starts the `poller` service only after the Redpanda healthcheck passes (`depends_on`).
- `apps/worker/poller.py` runs the scheduled batches first, then builds a Kafka producer for the
  publish step. The producer's constructor auto-detects the broker version using its library defaults.
  With the broker down, construction fails, the tick logs `postgres_poller_iteration_failed`, and
  the publish step is skipped. The rows stay unpublished and are sent once the broker is back.
- The batches are not gated on the broker. An ordinary batch failure is logged as
  `postgres_poller_task_failed` and later tasks still run. Each attempted task advances only its
  own deadline, so skipped hourly forecasts and five-second expiry scans are not postponed by
  one-second ticks. The outbox step runs after the batches; its failure leaves those deadlines
  intact. Injected process crashes still escape immediately.

`tests/unit/test_poller_scheduling.py` exercises cadence, batch failure isolation, a simulated
publisher-construction outage, and injected crashes. It does not stop a live broker. Nothing in
this repository consumes the stream, and financial correctness does not depend on it.

## Inbound connector event backlog

Accepted events from `POST /api/inbound/v1/connectors/{connector_id}/events` are processed by the
poller task `process_inbound_webhooks`, on a two-second cadence. Each run claims at most 25 events.
The rest stay queued for a later run, so a steady stream cannot hold one run in the claim loop and
starve the tasks scheduled after it. A backlog that does not drain means the poller is not running
or is falling behind; check its logs.

## Chaos runs

`make chaos` runs `scripts.chaos.run --mode ci --seeds 20 --ops 1000`. `make chaos-full` runs
`--mode full --seeds 200 --ops 10000`, which is manual only.

- The summary is written to `output/chaos/summary-ci-<UTC timestamp>.json`.
- Each seed reports `invariants: N violations`. A seed with violations is dumped to
  `output/chaos/seed-<n>.json`; the `violations` list names the invariant code and the offending row.
  Reproduce it with the `repro seed` line the run prints.
- Exit status is `1` when any seed has a harness failure, such as an unexpected exception, an
  operation that does not converge after quiesce, or a broken provider exactly-once check. Otherwise
  it is `0`. `--require-invariants` returns `2` if the checker cannot run.
- A failing seed writes `output/chaos/seed-<N>.json`. It holds the seed's summary, its failure
  messages (at most 25), any violations, the operation schedule, and the operation log. The run
  prints a repro line: `uv run python -m scripts.chaos.run --mode ci --seeds-list <N> --ops 1000`.

To read a dump, start with `failures`. Then find the first failing step in `log`, and look up that
step's operation in `schedule`. The `findings (not failures)` line is printed only when a findings
counter is non-empty. No code path currently increments it.
