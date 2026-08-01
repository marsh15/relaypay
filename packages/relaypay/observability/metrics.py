from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import ParamSpec, TypeVar

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

from relaypay.config import Settings

P = ParamSpec("P")
R = TypeVar("R")


class OperationsMetrics:
    """Low-cardinality metrics shared by API and worker instrumentation."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "relaypay_http_requests",
            "HTTP requests completed.",
            ("service", "method", "route", "status_class"),
            registry=self.registry,
        )
        self.http_latency = Histogram(
            "relaypay_http_request_duration_seconds",
            "HTTP request latency.",
            ("service", "method", "route"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=self.registry,
        )
        self.payments = Counter(
            "relaypay_payments_processed",
            "Payment operations processed.",
            ("kind", "outcome"),
            registry=self.registry,
        )
        self.payouts = Counter(
            "relaypay_payouts_processed",
            "Payout attempts processed.",
            ("outcome",),
            registry=self.registry,
        )
        self.webhooks = Counter(
            "relaypay_webhook_deliveries",
            "Webhook deliveries processed.",
            ("outcome",),
            registry=self.registry,
        )
        self.mismatches = Gauge(
            "relaypay_reconciliation_mismatches",
            "Current reconciliation mismatch count.",
            ("status",),
            registry=self.registry,
        )
        self.claim_depth = Gauge(
            "relaypay_claim_depth",
            "Current claimable work depth.",
            ("queue",),
            registry=self.registry,
        )
        self.worker_latency = Histogram(
            "relaypay_worker_processing_seconds",
            "Worker task processing latency.",
            ("task",),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
            registry=self.registry,
        )
        self.recoveries = Counter(
            "relaypay_recoveries",
            "Recovery and expired-lease reclaim operations.",
            ("kind",),
            registry=self.registry,
        )
        self.circuit_state = Gauge(
            "relaypay_connector_circuit_state",
            "Connector circuit state as a one-hot gauge.",
            ("connector", "state"),
            registry=self.registry,
        )

    def observe_http(
        self,
        *,
        service: str,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        status_class = f"{status_code // 100}xx"
        self.http_requests.labels(service, method, route, status_class).inc()
        self.http_latency.labels(service, method, route).observe(duration_seconds)

    @contextmanager
    def worker_timer(self, task: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self.worker_latency.labels(task).observe(time.monotonic() - started)

    def render(self) -> bytes:
        return generate_latest(self.registry)


_metrics: OperationsMetrics | None = None
_metrics_lock = threading.Lock()


def operations_metrics() -> OperationsMetrics:
    global _metrics
    with _metrics_lock:
        if _metrics is None:
            _metrics = OperationsMetrics()
        return _metrics


def observe_worker_task(task: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def measured(*args: P.args, **kwargs: P.kwargs) -> R:
            with operations_metrics().worker_timer(task):
                return function(*args, **kwargs)

        return measured

    return decorate


def install_asgi_metrics(app: FastAPI, settings: Settings, *, service: str) -> None:
    metrics = operations_metrics()

    @app.middleware("http")
    async def observe(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        response = await call_next(request)
        route_object = request.scope.get("route")
        route = getattr(route_object, "path", request.url.path)
        metrics.observe_http(
            service=service,
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_seconds=time.monotonic() - started,
        )
        return response

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        if not settings.OBSERVABILITY_ENABLED:
            return Response(status_code=404)
        return Response(
            content=metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )


def start_worker_metrics_server(port: int) -> None:
    start_http_server(port, registry=operations_metrics().registry)
