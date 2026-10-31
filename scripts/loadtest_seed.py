"""Provision a fresh synthetic TEST tenant; write its short-lived credentials privately."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from relaypay.config import get_settings
from relaypay.contracts import CustomerCreate
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import APIKey, APIKeyVersion, Environment, Organisation
from relaypay.identity.security import issue_api_key
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
from relaypay.merchant_balances.service import ensure_default_merchant_account
from relaypay.payments.service import create_customer
from sqlalchemy import select


def seed_load_tenant() -> dict[str, str]:
    settings = get_settings()
    if settings.APP_ENV == "production":
        raise ValueError("load seeding is restricted to development and test")
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="relaypay-load-seed"
    )
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            organisation = Organisation(
                public_id=new_public_id("org"), name="Synthetic load test", status="ACTIVE"
            )
            session.add(organisation)
            session.flush()
            environment = session.scalar(
                select(Environment).where(
                    Environment.organisation_id == organisation.id,
                    Environment.environment_type == "TEST",
                )
            )
            if environment is None:
                raise RuntimeError("TEST environment was not provisioned")
            merchant = ensure_default_merchant_account(
                session, organisation_id=organisation.id, environment_id=environment.id
            )
            session.add(
                LedgerAccount(
                    organisation_id=organisation.id,
                    environment_id=environment.id,
                    code="PROVIDER_CLEARING_ASSET",
                    name="Provider clearing",
                    account_type="ASSET",
                    currency="INR",
                )
            )
            issued, digest = issue_api_key(
                pepper=settings.API_KEY_PEPPER.get_secret_value(), environment_type="TEST"
            )
            key = APIKey(
                public_id=new_public_id("key"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                name="Synthetic load test",
                scopes=["customers:write", "payments:read", "payments:write"],
                status="ACTIVE",
            )
            session.add(key)
            session.flush()
            session.add(
                APIKeyVersion(
                    organisation_id=organisation.id,
                    environment_id=environment.id,
                    api_key_id=key.id,
                    version=1,
                    public_prefix=issued.public_prefix,
                    secret_digest=digest,
                    status="ACTIVE",
                    activated_at=datetime.now(UTC),
                )
            )
        customer = create_customer(
            factory,
            organisation_id=organisation.id,
            environment_id=environment.id,
            payload=CustomerCreate(merchant_customer_reference="load-customer"),
        )
        return {
            "apiKey": issued.plaintext,
            "customerId": customer.public_id,
            "organisationId": organisation.public_id,
            "environmentId": environment.public_id,
            "merchantAccountId": merchant.public_id,
        }
    finally:
        engine.dispose()


def write_credentials(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite existing files or follow a symlink; never print the key.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_credentials(args.output, seed_load_tenant())
    print(f"Synthetic TEST tenant credentials written to {args.output}")


if __name__ == "__main__":
    main()
