from __future__ import annotations

from dataclasses import dataclass

from relaypay.config import get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import APIKey, Organisation, User
from relaypay.identity.security import hash_password, issue_api_key
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
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
            session.add(
                User(
                    organisation_id=organisation.id,
                    email_normalized=demo.email.casefold(),
                    display_name=f"{demo.name} administrator",
                    password_hash=hash_password(demo.password),
                    role="ADMIN",
                    status="ACTIVE",
                )
            )
            issued, digest = issue_api_key(pepper=settings.API_KEY_PEPPER.get_secret_value())
            session.add(
                APIKey(
                    organisation_id=organisation.id,
                    name="Seeded merchant key",
                    public_prefix=issued.public_prefix,
                    secret_digest=digest,
                    scopes=["customers:write", "payments:read", "payments:write"],
                    status="ACTIVE",
                )
            )
            session.add_all(
                [
                    LedgerAccount(
                        organisation_id=organisation.id,
                        code="PROVIDER_CLEARING_ASSET",
                        name="Provider clearing",
                        account_type="ASSET",
                        currency="INR",
                    ),
                    LedgerAccount(
                        organisation_id=organisation.id,
                        code="MERCHANT_PAYABLE_LIABILITY",
                        name="Merchant payable",
                        account_type="LIABILITY",
                        currency="INR",
                    ),
                ]
            )
            issued_keys.append((demo, issued.plaintext))
    engine.dispose()
    return issued_keys


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
