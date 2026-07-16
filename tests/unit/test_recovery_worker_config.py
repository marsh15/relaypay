import importlib

from pytest import MonkeyPatch
from relaypay.config import get_settings


def test_recovery_worker_uses_redis_as_broker_only_and_late_acknowledgement(
    monkeypatch: MonkeyPatch,
) -> None:
    values = {
        "RELAYPAY_DATABASE_URL": "postgresql+psycopg://app:test@localhost/relaypay",
        "PROVIDER_DATABASE_URL": "postgresql+psycopg://provider:test@localhost/provider",
        "RECEIVER_DATABASE_URL": "postgresql+psycopg://receiver:test@localhost/relaypay",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "SESSION_SECRET": "session-secret-at-least-thirty-two-bytes",
        "CSRF_SECRET": "csrf-secret-at-least-thirty-two-bytes",
        "API_KEY_PEPPER": "api-key-pepper-at-least-thirty-two-bytes",
        "IDEMPOTENCY_KEY_PEPPER": "idempotency-pepper",
        "WEBHOOK_SECRET_ENCRYPTION_KEY": "webhook-encryption-test-key",
        "PROVIDER_SIGNING_SECRET": "provider-signing-test",
        "PROVIDER_CONTROL_SECRET": "provider-control-test",
        "RECEIVER_WEBHOOK_SECRET": "receiver-webhook-test",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    module = importlib.import_module("apps.worker.celery_app")

    assert module.app.conf.broker_url == "redis://localhost:6379/0"
    assert module.app.conf.result_backend is None
    assert module.app.conf.task_ignore_result is True
    assert module.app.conf.task_acks_late is True
    assert module.app.conf.task_reject_on_worker_lost is True
    assert module.app.conf.worker_prefetch_multiplier == 1
    assert module.app.conf.beat_schedule["poll-provider-recovery"]["schedule"] == 1.0

    get_settings.cache_clear()
