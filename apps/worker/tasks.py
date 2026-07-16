from relaypay.config import get_settings
from relaypay.database import build_engine, build_session_factory
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
