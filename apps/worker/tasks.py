from relaypay.config import get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.delivery import HTTPWebhookTransport, run_delivery_batch
from relaypay.event_delivery.materializer import materialize_deliveries
from relaypay.provider_operations.recovery import run_recovery_batch
from relaypay.provider_operations.service import HTTPProviderTransport

from apps.worker.celery_app import app


@app.task(name="relaypay.recover_provider_operations")  # type: ignore[untyped-decorator]
def recover_provider_operations() -> int:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-recovery-worker",
    )
    try:
        return run_recovery_batch(
            build_session_factory(engine),
            provider_account_id=settings.PROVIDER_ACCOUNT_ID,
            provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
            transport=HTTPProviderTransport(base_url=settings.PROVIDER_BASE_URL),
        )
    finally:
        engine.dispose()


@app.task(name="relaypay.materialize_webhook_deliveries")  # type: ignore[untyped-decorator]
def materialize_webhook_deliveries() -> int:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-materializer-worker",
    )
    try:
        return materialize_deliveries(build_session_factory(engine))
    finally:
        engine.dispose()


@app.task(name="relaypay.deliver_webhooks")  # type: ignore[untyped-decorator]
def deliver_webhooks() -> int:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-delivery-worker",
    )
    try:
        receiver_url = f"{settings.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay"
        return run_delivery_batch(
            build_session_factory(engine),
            encryption_key=settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
            transport=HTTPWebhookTransport(allowed_url=receiver_url),
        )
    finally:
        engine.dispose()
