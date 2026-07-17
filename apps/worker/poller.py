import logging
import time

from relaypay.config import Settings, get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.delivery import HTTPWebhookTransport, run_delivery_batch
from relaypay.event_delivery.materializer import materialize_deliveries
from relaypay.provider_operations.recovery import run_recovery_batch
from relaypay.provider_operations.service import HTTPProviderTransport

logger = logging.getLogger(__name__)


def poll_once(settings: Settings) -> dict[str, int]:
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-postgres-poller",
    )
    factory = build_session_factory(engine)
    try:
        recovered = run_recovery_batch(
            factory,
            provider_account_id=settings.PROVIDER_ACCOUNT_ID,
            provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
            transport=HTTPProviderTransport(base_url=settings.PROVIDER_BASE_URL),
        )
        materialized = materialize_deliveries(factory)
        receiver_url = f"{settings.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay"
        delivered = run_delivery_batch(
            factory,
            encryption_key=settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
            transport=HTTPWebhookTransport(allowed_url=receiver_url),
        )
        return {"recovered": recovered, "materialized": materialized, "delivered": delivered}
    finally:
        engine.dispose()


def main() -> None:
    settings = get_settings()
    while True:
        try:
            poll_once(settings)
        except Exception:
            logger.exception("postgres_poller_iteration_failed")
        time.sleep(1)


if __name__ == "__main__":
    main()
