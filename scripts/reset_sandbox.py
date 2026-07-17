"""Coordinated destructive reset for RelayPay's synthetic sandbox data only."""

from __future__ import annotations

import os

from pydantic import SecretStr
from relaypay.config import Settings, get_settings
from sqlalchemy import create_engine, text

from scripts.seed import seed

CONFIRMATION = "RESET_SYNTHETIC_RELAYPAY"


def assert_reset_allowed(settings: Settings, environment: dict[str, str]) -> None:
    if environment.get("SANDBOX_RESET_CONFIRM") != CONFIRMATION:
        raise RuntimeError(f"set SANDBOX_RESET_CONFIRM={CONFIRMATION} to reset synthetic data")
    if settings.APP_ENV == "production" and environment.get("ALLOW_SANDBOX_RESET") != "true":
        raise RuntimeError("production reset requires ALLOW_SANDBOX_RESET=true")


def _url(value: SecretStr | None, fallback: SecretStr) -> str:
    return (value or fallback).get_secret_value()


def reset() -> None:
    settings = get_settings()
    assert_reset_allowed(settings, dict(os.environ))
    relay_url = _url(settings.RELAYPAY_MIGRATION_DATABASE_URL, settings.RELAYPAY_DATABASE_URL)
    provider_url = _url(settings.PROVIDER_MIGRATION_DATABASE_URL, settings.PROVIDER_DATABASE_URL)

    databases = (
        (settings.RECEIVER_DATABASE_URL.get_secret_value(), "TRUNCATE receiver.received_events"),
        (relay_url, "TRUNCATE organisations CASCADE"),
        (provider_url, "TRUNCATE provider_accounts CASCADE"),
    )
    for database_url, statement in databases:
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.execute(text(statement))
        finally:
            engine.dispose()
    issued = seed()
    if len(issued) != 2:
        raise RuntimeError("sandbox reset did not restore both seeded organisations")
    print("Synthetic RelayPay sandbox reset and two demo organisations restored.")


if __name__ == "__main__":
    reset()
