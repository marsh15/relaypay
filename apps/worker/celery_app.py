from celery import Celery  # type: ignore[import-untyped]
from celery.signals import (  # type: ignore[import-untyped]
    worker_process_init,
    worker_process_shutdown,
)
from opentelemetry import trace
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from relaypay.config import get_settings
from relaypay.observability.metrics import start_worker_metrics_server
from relaypay.observability.telemetry import configure_tracing

settings = get_settings()

app = Celery(
    "relaypay",
    broker=settings.CELERY_BROKER_URL.get_secret_value(),
    include=("apps.worker.tasks",),
)
app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "poll-provider-recovery": {
            "task": "relaypay.recover_provider_operations",
            "schedule": 1.0,
        },
        "materialize-webhook-deliveries": {
            "task": "relaypay.materialize_webhook_deliveries",
            "schedule": 1.0,
        },
        "deliver-webhooks": {
            "task": "relaypay.deliver_webhooks",
            "schedule": 1.0,
        },
        "reconcile-statements": {
            "task": "relaypay.reconcile_statements",
            "schedule": 1.0,
        },
        "dispatch-payouts": {
            "task": "relaypay.dispatch_payouts",
            "schedule": 1.0,
        },
        "process-inbound-webhooks": {
            "task": "relaypay.process_inbound_webhooks",
            "schedule": 2.0,
        },
    },
)


@worker_process_init.connect(weak=False)  # type: ignore[untyped-decorator]
def initialize_worker_telemetry(**_: object) -> None:
    resolved = get_settings()
    provider = configure_tracing(resolved, service_name="relaypay-worker")
    if provider is not None:
        CeleryInstrumentor().instrument(tracer_provider=provider)  # type: ignore[no-untyped-call]
        start_worker_metrics_server(resolved.PROMETHEUS_WORKER_PORT)


@worker_process_shutdown.connect(weak=False)  # type: ignore[untyped-decorator]
def flush_worker_telemetry(**_: object) -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.force_flush(timeout_millis=5_000)
