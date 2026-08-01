from __future__ import annotations

import logging
import threading

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy import Engine

from relaypay.config import Settings

logger = logging.getLogger("relaypay.telemetry")
_configured = False
_httpx_instrumented = False
_lock = threading.Lock()


def configure_tracing(settings: Settings, *, service_name: str) -> TracerProvider | None:
    """Configure one process-wide OTLP provider only when explicitly enabled."""

    global _configured
    if not settings.OBSERVABILITY_ENABLED:
        return None
    with _lock:
        if _configured:
            provider = trace.get_tracer_provider()
            return provider if isinstance(provider, TracerProvider) else None
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        endpoint = f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip('/')}/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _configured = True
        logger.info("otel_configured service=%s endpoint=%s", service_name, endpoint)
        return provider


def instrument_fastapi(
    app: FastAPI,
    settings: Settings,
    *,
    service_name: str,
    engine: Engine | None = None,
) -> None:
    global _httpx_instrumented
    provider = configure_tracing(settings, service_name=service_name)
    if provider is None:
        return
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="/health/live,/health/ready,/metrics",
    )
    if engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
    with _lock:
        if not _httpx_instrumented:
            HTTPXClientInstrumentor().instrument(tracer_provider=provider)
            _httpx_instrumented = True


def instrument_sqlalchemy_engine(engine: Engine) -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
