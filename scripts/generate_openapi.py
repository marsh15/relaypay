import argparse
import json
from pathlib import Path
from typing import Any

from relaypay.config import Settings

from apps.api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "contracts/openapi/v0.9.0.json"
BASELINE = ROOT / "contracts/openapi/api-v1-v0.6.0.json"


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL="postgresql+psycopg://relaypay:relaypay@localhost/relaypay",
        PROVIDER_DATABASE_URL="postgresql+psycopg://provider:provider@localhost/provider",
        RECEIVER_DATABASE_URL="postgresql+psycopg://receiver:receiver@localhost/receiver",
        SESSION_SECRET="openapi-session-secret-at-least-32-bytes",  # noqa: S106
        CSRF_SECRET="openapi-csrf-secret-at-least-32-bytes",  # noqa: S106
        API_KEY_PEPPER="openapi-api-key-pepper-at-least-32-bytes",
        IDEMPOTENCY_KEY_PEPPER="openapi-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY="openapi-webhook-encryption-key",  # noqa: S106
        PROVIDER_SIGNING_SECRET="openapi-provider-signing-secret",  # noqa: S106
        PROVIDER_CONTROL_SECRET="openapi-provider-control-secret",  # noqa: S106
        RECEIVER_WEBHOOK_SECRET="openapi-receiver-webhook-secret",  # noqa: S106
    )


def _document(*, baseline: bool) -> dict[str, Any]:
    document: dict[str, Any] = create_app(_settings()).openapi()
    if baseline:
        document["info"]["version"] = "0.6.0"
        document["paths"]["/api/v1/payment_intents"].pop("get")
    return document


def _serialized(*, baseline: bool) -> bytes:
    return (
        json.dumps(_document(baseline=baseline), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = BASELINE if args.baseline else CURRENT
    generated = _serialized(baseline=args.baseline)
    if args.check:
        if not target.exists() or target.read_bytes() != generated:
            raise SystemExit(f"OpenAPI drift detected: regenerate {target.relative_to(ROOT)}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(generated)


if __name__ == "__main__":
    main()
