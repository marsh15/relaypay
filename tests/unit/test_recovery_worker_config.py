import importlib

from pytest import MonkeyPatch
from relaypay.config import Settings, get_settings


def test_single_broker_less_scheduler_owns_every_background_batch(
    monkeypatch: MonkeyPatch,
) -> None:
    values = {
        "RELAYPAY_DATABASE_URL": "postgresql+psycopg://app:test@localhost/relaypay",
        "PROVIDER_DATABASE_URL": "postgresql+psycopg://provider:test@localhost/provider",
        "RECEIVER_DATABASE_URL": "postgresql+psycopg://receiver:test@localhost/relaypay",
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
    poller = importlib.import_module("apps.worker.poller")

    # No broker exists: the poller is the only scheduler, and no Celery/
    # Redis settings remain on Settings at all.
    assert not any("CELERY" in name or "REDIS" in name for name in Settings.model_fields)

    # Every shipped background batch runs on the poller time-gated schedule.
    expected = {
        "recover_provider_operations": 1.0,
        "materialize_webhook_deliveries": 1.0,
        "deliver_webhooks": 1.0,
        "reconcile_statements": 1.0,
        "dispatch_payouts": 1.0,
        "run_subscription_recovery": 1.0,
        "process_inbound_webhooks": 2.0,
        "record_settlement_forecasts": 3600.0,
    }
    assert expected == poller.SCHEDULE_SECONDS
    assert set(poller._RUNNERS) | {"process_inbound_webhooks"} == set(expected)

    get_settings.cache_clear()
