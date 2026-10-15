# Operations telemetry and measured evidence

RelayPay telemetry is off by default. Set `OBSERVABILITY_ENABLED=true` and enable the Compose
`observability` profile to run the OpenTelemetry Collector, Prometheus, and provisioned Grafana
dashboard.

The API exposes `/metrics` only while observability is enabled. Histograms use bounded route
templates rather than raw paths. Request logs store only request ID, tenant/environment IDs when
known, method, route template, status, duration, and timestamp. Authorization headers, API keys,
cookies, arbitrary headers, and bodies are never persisted. `REQUEST_LOG_RETENTION` bounds raw
metadata rows; hourly rollups remain durable.

OpenTelemetry uses OTLP/HTTP and batched span export. The API, simulators, receiver, and poller
each publish an explicit service name (the poller calls `configure_tracing` at start-up). The
bundled collector has a traces pipeline only and exports to its `debug` exporter; there is no
persistent trace backend, and no metrics or logs pipeline.

Prometheus (`infra/observability/prometheus.yaml`) scrapes `api:8000`, and `provider`, `receiver`,
`bank`, and `commerce` on their service ports. It does not scrape the dispute, recovery, or
merchant-site simulators. Its `relaypay-worker` job targets `worker:9100`, a service that no longer
exists since the scheduler consolidation in v1.0.1; the poller serves metrics on
`PROMETHEUS_WORKER_PORT` (default 9100) under the service name `poller`, so this configuration does
not collect poller metrics until that target is renamed. The Grafana dashboard "RelayPay
Operations Evidence" is provisioned read-only with anonymous viewer access.

`/metrics` on the API and the poller port are unauthenticated and expose route shapes, latencies,
and counters. Keep them on an internal network ([limitations](../limitations.md)).

The canonical gate produces:

- `performance.json`: real bounded HTTP request count, throughput, and p50/p95/p99 latency;
- `failure-report.json`: measured test durations for the six named failure scenarios;
- `query-plans.json`: PostgreSQL before/after index plans over 5,000 rollback-only rows;
- `metrics.txt` and `otel-collector.log`: scrape and trace-receipt proof.

These are measured development/CI observations. RelayPay defines no production SLO from them.
The workload is intentionally bounded and contains synthetic data only.
