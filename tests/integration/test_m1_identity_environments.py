import uuid

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.identity.models import (
    APIKeyVersion,
    AuditRecord,
    Environment,
    Organisation,
    OrganisationMembership,
    User,
)
from relaypay.identity.security import Principal, authenticate_api_key, hash_password
from relaypay.identity.service import (
    activate_api_key_version,
    create_api_key,
    revoke_api_key,
    rotate_api_key,
    set_api_key_scopes,
    set_membership,
)
from relaypay.ids import new_public_id
from sqlalchemy import select

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
PEPPER = "m1-api-key-pepper-at-least-32-bytes"


def _principal(
    organisation: Organisation, user: User, *, role: str = "ORGANISATION_ADMIN"
) -> Principal:
    return Principal(
        kind="SESSION",
        organisation_id=organisation.id,
        organisation_public_id=organisation.public_id,
        environment_id=None,
        environment_public_id=None,
        display_name=user.display_name,
        scopes=frozenset(),
        membership_role=role,
        user_id=user.id,
    )


def test_memberships_key_rotation_scopes_revocation_and_audits() -> None:
    engine = build_engine(DATABASE_URL, application_name="m1-identity-test")
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="M1 identity test", status="ACTIVE"
        )
        admin = User(
            email_normalized=f"admin-{uuid.uuid4().hex}@example.test",
            display_name="M1 admin",
            password_hash=hash_password("Synthetic-M1-Admin-Password!"),
            platform_role="STANDARD",
            status="ACTIVE",
        )
        developer = User(
            email_normalized=f"developer-{uuid.uuid4().hex}@example.test",
            display_name="M1 developer",
            password_hash=hash_password("Synthetic-M1-Developer-Password!"),
            platform_role="STANDARD",
            status="ACTIVE",
        )
        session.add_all([organisation, admin, developer])
        session.flush()
        session.add(
            OrganisationMembership(
                organisation_id=organisation.id,
                user_id=admin.id,
                role="ORGANISATION_ADMIN",
                status="ACTIVE",
            )
        )
        session.flush()
        environments = list(
            session.scalars(
                select(Environment).where(Environment.organisation_id == organisation.id)
            )
        )
        assert {item.environment_type for item in environments} == {"TEST", "LIVE_LIKE"}
        test_environment = next(item for item in environments if item.environment_type == "TEST")
        live_environment = next(
            item for item in environments if item.environment_type == "LIVE_LIKE"
        )
        principal = _principal(organisation, admin)
        membership = set_membership(
            session,
            principal=principal,
            email=developer.email_normalized,
            role="DEVELOPER",
            status="ACTIVE",
        )
        assert membership.role == "DEVELOPER"
        key, version_one, issued_one = create_api_key(
            session,
            principal=principal,
            environment_public_id=test_environment.public_id,
            name="M1 test key",
            scopes=["payments:read"],
            pepper=PEPPER,
        )
        assert version_one.status == "ACTIVE"
        version_two, issued_two = rotate_api_key(
            session,
            principal=principal,
            environment_public_id=test_environment.public_id,
            key_public_id=key.public_id,
            pepper=PEPPER,
        )
        assert version_two.status == "PENDING"

    with factory() as session, session.begin():
        assert (
            authenticate_api_key(
                session, plaintext=issued_one.plaintext, pepper=PEPPER
            ).environment_public_id
            == test_environment.public_id
        )
        with pytest.raises(RelayPayError):
            authenticate_api_key(session, plaintext=issued_two.plaintext, pepper=PEPPER)
        activate_api_key_version(
            session,
            principal=principal,
            environment_public_id=test_environment.public_id,
            key_public_id=key.public_id,
            version_number=2,
        )
        set_api_key_scopes(
            session,
            principal=principal,
            environment_public_id=test_environment.public_id,
            key_public_id=key.public_id,
            scopes=["payments:read", "payments:write"],
        )
        with pytest.raises(RelayPayError) as cross_environment:
            set_api_key_scopes(
                session,
                principal=principal,
                environment_public_id=live_environment.public_id,
                key_public_id=key.public_id,
                scopes=["payments:read"],
            )
        assert cross_environment.value.http_status == 404

    with factory() as session, session.begin():
        with pytest.raises(RelayPayError):
            authenticate_api_key(session, plaintext=issued_one.plaintext, pepper=PEPPER)
        active = authenticate_api_key(session, plaintext=issued_two.plaintext, pepper=PEPPER)
        assert active.scopes == frozenset({"payments:read", "payments:write"})
        revoke_api_key(
            session,
            principal=principal,
            environment_public_id=test_environment.public_id,
            key_public_id=key.public_id,
        )

    with factory() as session, session.begin():
        with pytest.raises(RelayPayError):
            authenticate_api_key(session, plaintext=issued_two.plaintext, pepper=PEPPER)
        versions = list(
            session.scalars(select(APIKeyVersion).where(APIKeyVersion.api_key_id == key.id))
        )
        assert {item.status for item in versions} == {"REVOKED"}
        actions = set(session.scalars(select(AuditRecord.action)))
        assert {
            "MEMBERSHIP_CREATED",
            "API_KEY_CREATED",
            "API_KEY_ROTATED",
            "API_KEY_VERSION_ACTIVATED",
            "API_KEY_SCOPES_CHANGED",
            "API_KEY_REVOKED",
        } <= actions
        with pytest.raises(RelayPayError) as denied:
            set_membership(
                session,
                principal=_principal(organisation, developer, role="DEVELOPER"),
                email=admin.email_normalized,
                role="VIEWER",
                status="ACTIVE",
            )
        assert denied.value.http_status == 403
    engine.dispose()
