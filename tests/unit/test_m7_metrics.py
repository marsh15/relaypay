from contextlib import suppress
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.observability import metrics as metrics_module
from relaypay.observability.metrics import (
    OTHER_METHOD,
    UNMATCHED_ROUTE,
    OperationsMetrics,
    install_asgi_metrics,
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


def _request_counts(metrics: OperationsMetrics) -> dict[tuple[str, str, str], float]:
    counts: dict[tuple[str, str, str], float] = {}
    for family in metrics.http_requests.collect():
        for sample in family.samples:
            if sample.name == "relaypay_http_requests_total":
                labels = sample.labels
                counts[(labels["method"], labels["route"], labels["status_class"])] = sample.value
    return counts


def test_unmatched_paths_and_custom_methods_do_not_grow_label_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = OperationsMetrics()
    monkeypatch.setattr(metrics_module, "_metrics", metrics)
    app = FastAPI()

    @app.get("/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    install_asgi_metrics(app, MagicMock(spec=list(Settings.model_fields)), service="test")
    client = TestClient(app)
    for item_id in ("a", "b"):
        assert client.get(f"/items/{item_id}").status_code == 200
    for probe in ("/nope/1", "/nope/2", "/.env", "/admin.php"):
        assert client.get(probe).status_code == 404
    for method in ("FOO", "BAR", "PROPFIND"):
        client.request(method, "/items/a")

    counts = _request_counts(metrics)
    # Two parameter values share one template series; four distinct probes share one sentinel.
    assert counts[("GET", "/items/{item_id}", "2xx")] == 2
    assert counts[("GET", UNMATCHED_ROUTE, "4xx")] == 4
    other = [count for (method, _, _), count in counts.items() if method == OTHER_METHOD]
    assert sum(other) == 3
    assert {method for method, _, _ in counts} <= {"GET", OTHER_METHOD}
    assert {route for _, route, _ in counts} <= {"/items/{item_id}", UNMATCHED_ROUTE}


def test_observe_http_keeps_standard_methods_and_collapses_the_rest() -> None:
    metrics = OperationsMetrics()
    for method in ("PATCH", "PROPFIND", "X-CUSTOM", "get", "FOO"):
        metrics.observe_http(
            service="api",
            method=method,
            route="/health/live",
            status_code=405,
            duration_seconds=0.01,
        )
    assert _request_counts(metrics) == {
        ("PATCH", "/health/live", "4xx"): 1,
        (OTHER_METHOD, "/health/live", "4xx"): 4,
    }
