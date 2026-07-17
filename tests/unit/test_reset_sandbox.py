import pytest
from relaypay.config import Settings

from scripts.reset_sandbox import CONFIRMATION, assert_reset_allowed


def _settings(app_env: str = "test") -> Settings:
    return Settings(
        APP_ENV=app_env,
        PUBLIC_BASE_URL="https://sandbox.example.test" if app_env == "production" else "http://test",
        RELAYPAY_DATABASE_URL="postgresql+psycopg://unused",
        PROVIDER_DATABASE_URL="postgresql+psycopg://unused",
        RECEIVER_DATABASE_URL="postgresql+psycopg://unused",
        SESSION_SECRET="s" * 32,
        CSRF_SECRET="c" * 32,
        API_KEY_PEPPER="a" * 32,
        IDEMPOTENCY_KEY_PEPPER="i" * 16,
        WEBHOOK_SECRET_ENCRYPTION_KEY="synthetic-key",
        PROVIDER_SIGNING_SECRET="provider-signing",
        PROVIDER_CONTROL_SECRET="provider-control",
        RECEIVER_WEBHOOK_SECRET="receiver-webhook",
    )


def test_reset_requires_exact_confirmation_and_explicit_production_override() -> None:
    with pytest.raises(RuntimeError, match="SANDBOX_RESET_CONFIRM"):
        assert_reset_allowed(_settings(), {})

    assert_reset_allowed(_settings(), {"SANDBOX_RESET_CONFIRM": CONFIRMATION})

    with pytest.raises(RuntimeError, match="production reset"):
        assert_reset_allowed(_settings("production"), {"SANDBOX_RESET_CONFIRM": CONFIRMATION})
    assert_reset_allowed(
        _settings("production"),
        {"SANDBOX_RESET_CONFIRM": CONFIRMATION, "ALLOW_SANDBOX_RESET": "true"},
    )
