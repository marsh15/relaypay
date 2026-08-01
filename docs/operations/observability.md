# Operations telemetry and measured evidence

RelayPay telemetry is off by default. Set `OBSERVABILITY_ENABLED=true` and enable the Compose
`observability` profile to run the OpenTelemetry Collector, Prometheus, and provisioned Grafana
dashboard.

The API exposes `/metrics` only while observability is enabled. Histograms use bounded route
templates rather than raw paths. Request logs store only request ID, tenant/environment IDs when
known, method, route template, status, duration, and timestamp. Authorization headers, API keys,
cookies, arbitrary headers, and bodies are never persisted. `REQUEST_LOG_RETENTION` bounds raw
metadata rows; hourly rollups remain durable.

OpenTelemetry uses OTLP/HTTP and batched span export. API, mock, receiver, poller, and worker
processes each publish an explicit service name. Celery instrumentation initializes from
`worker_process_init` and flushes on worker-process shutdown.

The canonical gate produces:

- `performance.json`: real bounded HTTP request count, throughput, and p50/p95/p99 latency;
- `failure-report.json`: measured test durations for the six named failure scenarios;
- `query-plans.json`: PostgreSQL before/after index plans over 5,000 rollback-only rows;
- `metrics.txt` and `otel-collector.log`: scrape and trace-receipt proof.

These are measured development/CI observations. RelayPay defines no production SLO from them.
The workload is intentionally bounded and contains synthetic data only.
