from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from relaypay.config import Settings, get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.crypto import encrypt_webhook_secret
from relaypay.event_delivery.models import WebhookEndpoint, WebhookEndpointVersion
from relaypay.identity.models import (
    APIKey,
    APIKeyVersion,
    Environment,
    Organisation,
    OrganisationMembership,
    User,
)
from relaypay.identity.security import hash_password, issue_api_key
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
from relaypay.mock_provider.models import ProviderAccount
from sqlalchemy import select


@dataclass(frozen=True, slots=True)
class DemoOrganisation:
    name: str
    email: str
    password: str


DEMO_ORGANISATIONS = (
    DemoOrganisation("Northstar Demo", "admin@northstar.test", "RelayPay-Northstar-2026!"),
    DemoOrganisation("Juniper Demo", "admin@juniper.test", "RelayPay-Juniper-2026!"),
)


def seed() -> list[tuple[DemoOrganisation, str]]:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="relaypay-seed"
    )
    factory = build_session_factory(engine)
    issued_keys: list[tuple[DemoOrganisation, str]] = []
    with factory() as session, session.begin():
        for demo in DEMO_ORGANISATIONS:
            existing = session.scalar(
                select(User).where(User.email_normalized == demo.email.casefold())
            )
            if existing is not None:
                continue
            organisation = Organisation(
                public_id=new_public_id("org"), name=demo.name, status="ACTIVE"
            )
            session.add(organisation)
            session.flush()
            environments = list(
                session.scalars(
                    select(Environment).where(Environment.organisation_id == organisation.id)
                )
            )
            if {item.environment_type for item in environments} != {"TEST", "LIVE_LIKE"}:
                raise RuntimeError("organisation environments were not provisioned")
            test_environment = next(
                item for item in environments if item.environment_type == "TEST"
            )
            user = User(
                email_normalized=demo.email.casefold(),
                display_name=f"{demo.name} administrator",
                password_hash=hash_password(demo.password),
                platform_role="STANDARD",
                status="ACTIVE",
            )
            session.add(user)
            session.flush()
            session.add(
                OrganisationMembership(
                    organisation_id=organisation.id,
                    user_id=user.id,
                    role="ORGANISATION_ADMIN",
                    status="ACTIVE",
                )
            )
            issued, digest = issue_api_key(
                pepper=settings.API_KEY_PEPPER.get_secret_value(), environment_type="TEST"
            )
            api_key = APIKey(
                public_id=new_public_id("key"),
                organisation_id=organisation.id,
                environment_id=test_environment.id,
                name="Seeded merchant key",
                scopes=["customers:write", "payments:read", "payments:write"],
                status="ACTIVE",
            )
            session.add(api_key)
            session.flush()
            session.add(
                APIKeyVersion(
                    organisation_id=organisation.id,
                    environment_id=test_environment.id,
                    api_key_id=api_key.id,
                    version=1,
                    public_prefix=issued.public_prefix,
                    secret_digest=digest,
                    status="ACTIVE",
                    activated_at=datetime.now(UTC),
                )
            )
            session.add_all(
                [
                    LedgerAccount(
                        organisation_id=organisation.id,
                        environment_id=test_environment.id,
                        code="PROVIDER_CLEARING_ASSET",
                        name="Provider clearing",
                        account_type="ASSET",
                        currency="INR",
                    ),
                    LedgerAccount(
                        organisation_id=organisation.id,
                        environment_id=test_environment.id,
                        code="MERCHANT_PAYABLE_LIABILITY",
                        name="Merchant payable",
                        account_type="LIABILITY",
                        currency="INR",
                    ),
                ]
            )
            issued_keys.append((demo, issued.plaintext))
        for organisation in session.scalars(select(Organisation).order_by(Organisation.id)):
            existing_test_environment = session.scalar(
                select(Environment).where(
                    Environment.organisation_id == organisation.id,
                    Environment.environment_type == "TEST",
                )
            )
            if existing_test_environment is None:
                raise RuntimeError("organisation is missing its TEST environment")
            endpoint = session.scalar(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.organisation_id == organisation.id,
                    WebhookEndpoint.environment_id == existing_test_environment.id,
                    WebhookEndpoint.name == "Bundled receiver",
                )
            )
            if endpoint is not None:
                continue
            endpoint = WebhookEndpoint(
                public_id=new_public_id("wh"),
                organisation_id=organisation.id,
                environment_id=existing_test_environment.id,
                name="Bundled receiver",
                status="ACTIVE",
            )
            session.add(endpoint)
            session.flush()
            session.add(
                WebhookEndpointVersion(
                    public_id=new_public_id("whv"),
                    organisation_id=organisation.id,
                    environment_id=existing_test_environment.id,
                    webhook_endpoint_id=endpoint.id,
                    version=1,
                    url=f"{settings.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay",
                    encrypted_secret=encrypt_webhook_secret(
                        settings.RECEIVER_WEBHOOK_SECRET.get_secret_value(),
                        settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
                    ),
                    subscribed_event_types=[
                        "payment.authorized.v1",
                        "payment.captured.v1",
                        "refund.succeeded.v1",
                    ],
                    active_from=datetime.now(UTC),
                )
            )
    engine.dispose()
    _seed_provider_account(settings)
    return issued_keys


def _seed_provider_account(settings: Settings) -> None:
    engine = build_engine(
        settings.PROVIDER_DATABASE_URL.get_secret_value(), application_name="relaypay-provider-seed"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        existing = session.scalar(
            select(ProviderAccount).where(ProviderAccount.public_id == settings.PROVIDER_ACCOUNT_ID)
        )
        if existing is None:
            session.add(
                ProviderAccount(
                    public_id=settings.PROVIDER_ACCOUNT_ID,
                    name="RelayPay deterministic provider account",
                    signing_secret_digest=hashlib.sha256(
                        settings.PROVIDER_SIGNING_SECRET.get_secret_value().encode("utf-8")
                    ).digest(),
                )
            )
    engine.dispose()


def main() -> None:
    issued = seed()
    if not issued:
        print("Demo organisations already exist; no API key material was reissued.")
        return
    print("Synthetic demo credentials (API keys are shown once):")
    for demo, api_key in issued:
        print(f"- {demo.name}: {demo.email} / {demo.password}")
        print(f"  API key: {api_key}")


if __name__ == "__main__":
    main()
