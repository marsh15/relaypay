from contextlib import suppress

from relaypay.observability.metrics import (
    OperationsMetrics,
    observe_worker_task,
    operations_metrics,
)


def test_prometheus_contract_has_bounded_operational_series() -> None:
    metrics = OperationsMetrics()
    metrics.observe_http(
        service="api",
        method="GET",
        route="/health/live",
        status_code=200,
        duration_seconds=0.01,
    )
    metrics.payments.labels("capture", "accepted").inc()
    metrics.payouts.labels("attempted").inc()
    metrics.webhooks.labels("retry").inc()
    metrics.mismatches.labels("open").set(2)
    metrics.claim_depth.labels("provider_recovery").set(3)
    metrics.recoveries.labels("expired_lease_reclaimed").inc()
    metrics.circuit_state.labels("provider", "open").set(1)
    rendered = metrics.render().decode()
    for series in (
        "relaypay_http_request_duration_seconds_bucket",
        "relaypay_payments_processed_total",
        "relaypay_payouts_processed_total",
        "relaypay_webhook_deliveries_total",
        "relaypay_reconciliation_mismatches",
        "relaypay_claim_depth",
        "relaypay_recoveries_total",
        "relaypay_connector_circuit_state",
    ):
        assert series in rendered


def test_worker_task_latency_is_observed_on_failure() -> None:
    @observe_worker_task("bounded_failure")
    def fail() -> None:
        raise RuntimeError("expected")

    with suppress(RuntimeError):
        fail()
    assert "relaypay_worker_processing_seconds_count" in operations_metrics().render().decode()
