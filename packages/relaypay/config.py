from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process configuration; secret values stay wrapped and out of repr/logs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    APP_ENV: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    PUBLIC_BASE_URL: str = "http://localhost:8080"
    RELAYPAY_DATABASE_URL: SecretStr
    RELAYPAY_MIGRATION_DATABASE_URL: SecretStr | None = None
    PROVIDER_DATABASE_URL: SecretStr
    PROVIDER_MIGRATION_DATABASE_URL: SecretStr | None = None
    BANK_DATABASE_URL: SecretStr = SecretStr(
        "postgresql+psycopg://bank_app:bank_app_dev@localhost:55432/bank"
    )
    BANK_MIGRATION_DATABASE_URL: SecretStr | None = None
    RECEIVER_DATABASE_URL: SecretStr
    REDIS_URL: SecretStr = SecretStr("redis://localhost:6379/0")
    CELERY_BROKER_URL: SecretStr = SecretStr("redis://localhost:6379/0")
    REDPANDA_BROKERS: str = "localhost:19092"
    AGENT_MODEL_PROVIDER: Literal["fake", "live"] = "fake"
    OPENAI_MODEL_ID: str = "gpt-5-mini-2025-08-07"
    CLAUDE_MODEL_ID: str = "claude-sonnet-4-20250514"
    GEMINI_MODEL_ID: str = "gemini-2.5-flash"
    SESSION_COOKIE_NAME: str = "relaypay_session"
    SESSION_SECRET: SecretStr = Field(min_length=32)
    CSRF_SECRET: SecretStr = Field(min_length=32)
    API_KEY_PEPPER: SecretStr = Field(min_length=32)
    IDEMPOTENCY_KEY_PEPPER: SecretStr = Field(min_length=16)
    WEBHOOK_SECRET_ENCRYPTION_KEY: SecretStr
    PROVIDER_BASE_URL: str = "http://localhost:8001"
    PROVIDER_ACCOUNT_ID: str = "acct_relaypay_demo"
    PROVIDER_SIGNING_SECRET: SecretStr = Field(min_length=16)
    PROVIDER_CONTROL_SECRET: SecretStr = Field(min_length=16)
    BANK_BASE_URL: str = "http://localhost:8003"
    BANK_ACCOUNT_ID: str = "bank_relaypay_demo"
    BANK_SIGNING_SECRET: SecretStr = SecretStr("dev-bank-signing-secret-change-me")
    BANK_CONTROL_SECRET: SecretStr = SecretStr("dev-bank-control-secret-change-me")
    COMMERCE_DATABASE_URL: SecretStr = SecretStr(
        "postgresql+psycopg://commerce_app:commerce_app_dev@localhost:55432/commerce"
    )
    COMMERCE_MIGRATION_DATABASE_URL: SecretStr | None = None
    COMMERCE_BASE_URL: str = "http://localhost:8004"
    COMMERCE_ACCOUNT_ID: str = "commerce_relaypay_demo"
    COMMERCE_CONTROL_SECRET: SecretStr = SecretStr("dev-commerce-control-secret-change-me")
    CONNECTOR_CREDENTIAL_ENCRYPTION_KEY: SecretStr = SecretStr(
        "dev-connector-credential-encryption-key"
    )
    DISPUTE_PACKAGE_SIGNING_SECRET: SecretStr = SecretStr(
        "dev-dispute-package-signing-secret-change-me"
    )
    DISPUTE_NETWORK_BASE_URL: str = "http://localhost:8005"
    INBOUND_WEBHOOK_REPLAY_SECONDS: int = 300
    RECEIVER_BASE_URL: str = "http://localhost:8002"
    RECEIVER_WEBHOOK_SECRET: SecretStr = Field(min_length=16)
    OBSERVABILITY_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://otel-collector:4318"
    PROMETHEUS_WORKER_PORT: int = Field(default=9100, ge=1024, le=65_535)
    REQUEST_LOG_RETENTION: int = Field(default=10_000, ge=100, le=1_000_000)
    EDGE_ORIGIN_SIGNATURE_REQUIRED: bool = False
    EDGE_ORIGIN_SIGNING_SECRET: SecretStr = SecretStr("dev-edge-origin-signing-secret-change-me")
    EDGE_ORIGIN_REPLAY_SECONDS: int = Field(default=300, ge=30, le=900)

    @model_validator(mode="after")
    def require_https_in_production(self) -> Self:
        if self.APP_ENV == "production" and not self.PUBLIC_BASE_URL.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must use HTTPS in production")
        if (
            self.EDGE_ORIGIN_SIGNATURE_REQUIRED
            and self.EDGE_ORIGIN_SIGNING_SECRET.get_secret_value()
            == "dev-edge-origin-signing-secret-change-me"
        ):
            raise ValueError("EDGE_ORIGIN_SIGNING_SECRET must be replaced when enforcement is on")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
