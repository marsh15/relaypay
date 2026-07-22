"""Run a synthetic provider-export to reconciliation-evidence journey."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx2
from relaypay.config import get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.reconciliation.service import run_reconciliation_batch


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Set {name} to a synthetic demo credential")
    return value


def main() -> None:
    settings = get_settings()
    api_base_url = os.getenv("RELAYPAY_API_BASE_URL", "http://localhost:8000").rstrip("/")
    email = os.getenv("RELAYPAY_DEMO_EMAIL", "admin@northstar.test")
    password = _required_environment("RELAYPAY_DEMO_PASSWORD")
    now = datetime.now(UTC)
    source_reference = f"synthetic-reconciliation-{uuid.uuid4().hex}"
    period_start = now - timedelta(days=1)
    period_end = now + timedelta(minutes=1)

    with httpx2.Client(base_url=api_base_url, timeout=10.0) as api:
        login = api.post("/api/session/login", json={"email": email, "password": password})
        login.raise_for_status()
        csrf_token = login.json()["csrfToken"]
        environments = api.get("/api/admin/v1/environments")
        environments.raise_for_status()
        test_environment = next(item for item in environments.json() if item["type"] == "TEST")

        provider_response = httpx2.post(
            f"{settings.PROVIDER_BASE_URL.rstrip('/')}/control/statements",
            headers={"X-Provider-Control": settings.PROVIDER_CONTROL_SECRET.get_secret_value()},
            json={
                "accountId": settings.PROVIDER_ACCOUNT_ID,
                "sourceReference": source_reference,
                "periodStart": period_start.isoformat(),
                "periodEnd": period_end.isoformat(),
            },
            timeout=10.0,
        )
        provider_response.raise_for_status()
        imported = api.post(
            f"/api/admin/v1/environments/{test_environment['id']}/statement-imports",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "provider": "PAYMENT_PROVIDER",
                "sourceReference": source_reference,
                "sourceFormat": "JSON",
                "periodStart": period_start.isoformat(),
                "periodEnd": period_end.isoformat(),
            },
            files={
                "statement": (
                    "provider-statement.json",
                    provider_response.content,
                    "application/json",
                )
            },
        )
        imported.raise_for_status()

        engine = build_engine(
            settings.RELAYPAY_DATABASE_URL.get_secret_value(),
            application_name="relaypay-reconciliation-demo",
        )
        try:
            run_reconciliation_batch(build_session_factory(engine))
        finally:
            engine.dispose()

        mismatches = api.get(
            f"/api/admin/v1/environments/{test_environment['id']}/reconciliation-mismatches"
        )
        mismatches.raise_for_status()
        print(
            json.dumps(
                {
                    "environmentId": test_environment["id"],
                    "importId": imported.json()["id"],
                    "itemCount": int(provider_response.headers["X-Statement-Item-Count"]),
                    "mismatchCountInEnvironment": len(mismatches.json()),
                    "runId": imported.json()["runId"],
                    "sourceReference": source_reference,
                    "statementSha256": imported.json()["sha256"],
                    "syntheticDataOnly": True,
                },
                indent=2,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
