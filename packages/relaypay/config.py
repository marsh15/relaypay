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
    RECEIVER_DATABASE_URL: SecretStr
    REDIS_URL: SecretStr = SecretStr("redis://localhost:6379/0")
    CELERY_BROKER_URL: SecretStr = SecretStr("redis://localhost:6379/0")
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
    RECEIVER_BASE_URL: str = "http://localhost:8002"
    RECEIVER_WEBHOOK_SECRET: SecretStr = Field(min_length=16)

    @model_validator(mode="after")
    def require_https_in_production(self) -> Self:
        if self.APP_ENV == "production" and not self.PUBLIC_BASE_URL.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must use HTTPS in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
