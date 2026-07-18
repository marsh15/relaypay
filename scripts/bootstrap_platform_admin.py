import os

from relaypay.config import get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import Organisation, OrganisationMembership, User
from relaypay.identity.security import hash_password
from relaypay.ids import new_public_id
from sqlalchemy import select


def main() -> None:
    email = os.environ["RELAYPAY_BOOTSTRAP_ADMIN_EMAIL"].strip().casefold()
    password = os.environ["RELAYPAY_BOOTSTRAP_ADMIN_PASSWORD"]
    display_name = os.environ.get("RELAYPAY_BOOTSTRAP_ADMIN_NAME", "Platform administrator")
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-platform-bootstrap",
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        existing = session.scalar(select(User).where(User.email_normalized == email))
        if existing is not None:
            if existing.platform_role != "PLATFORM_ADMIN":
                existing.platform_role = "PLATFORM_ADMIN"
            return
        organisation = Organisation(
            public_id=new_public_id("org"), name="RelayPay Platform", status="ACTIVE"
        )
        user = User(
            email_normalized=email,
            display_name=display_name,
            password_hash=hash_password(password),
            platform_role="PLATFORM_ADMIN",
            status="ACTIVE",
        )
        session.add_all([organisation, user])
        session.flush()
        session.add(
            OrganisationMembership(
                organisation_id=organisation.id,
                user_id=user.id,
                role="ORGANISATION_ADMIN",
                status="ACTIVE",
            )
        )
    engine.dispose()


if __name__ == "__main__":
    main()
