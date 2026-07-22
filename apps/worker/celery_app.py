from celery import Celery  # type: ignore[import-untyped]
from relaypay.config import get_settings

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
    },
)
