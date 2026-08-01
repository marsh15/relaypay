import uuid
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.identity.models import Environment, Organisation
from relaypay.identity.security import Principal
from relaypay.ids import new_public_id
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.operations.service import list_operations_resource

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"


def _scope(label: str) -> tuple[Principal, Environment]:
    engine = build_engine(DATABASE_URL, application_name=f"m8-operations-{label}")
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name=f"M8 {label}", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        environment = (
            session.query(Environment)
            .filter_by(
                organisation_id=organisation.id,
                environment_type="TEST",
            )
            .one()
        )
        default_merchant = (
            session.query(MerchantAccount)
            .filter_by(
                organisation_id=organisation.id,
                environment_id=environment.id,
                is_default=True,
            )
            .one()
        )
        default_merchant.reference = f"{label}-one"
        default_merchant.name = f"{label} one"
        now = datetime.now(UTC)
        default_merchant.created_at = now - timedelta(seconds=1)
        session.add(
            MerchantAccount(
                public_id=new_public_id("mac"),
                organisation_id=organisation.id,
                environment_id=environment.id,
                reference=f"{label}-two",
                name=f"{label} two",
                currency="INR",
                is_default=False,
                status="ACTIVE",
                created_at=now,
            )
        )
        principal = Principal(
            kind="SESSION",
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=None,
            environment_public_id=None,
            display_name="M8 operator",
            scopes=frozenset(),
            membership_role="ORGANISATION_ADMIN",
            user_id=uuid.uuid4(),
        )
    engine.dispose()
    return principal, environment


def test_operations_pages_are_cursor_bound_and_tenant_isolated() -> None:
    first_principal, first_environment = _scope("first")
    second_principal, second_environment = _scope("second")
    engine = build_engine(DATABASE_URL, application_name="m8-operations-read")
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        first = list_operations_resource(
            session,
            principal=first_principal,
            environment_public_id=first_environment.public_id,
            resource="merchant-accounts",
            limit=1,
            after=None,
            cursor_secret="m8-cursor-secret",
        )
        assert len(first.data) == 1
        assert first.next_cursor is not None
        assert str(first.data[0]["reference"]).startswith("first-")

        second = list_operations_resource(
            session,
            principal=first_principal,
            environment_public_id=first_environment.public_id,
            resource="merchant-accounts",
            limit=1,
            after=first.next_cursor,
            cursor_secret="m8-cursor-secret",
        )
        assert len(second.data) == 1
        assert second.data[0]["id"] != first.data[0]["id"]

        with pytest.raises(RelayPayError) as wrong_tenant:
            list_operations_resource(
                session,
                principal=second_principal,
                environment_public_id=first_environment.public_id,
                resource="merchant-accounts",
                limit=25,
                after=None,
                cursor_secret="m8-cursor-secret",
            )
        assert wrong_tenant.value.code == "RESOURCE_NOT_FOUND"

        with pytest.raises(RelayPayError) as rebound_cursor:
            list_operations_resource(
                session,
                principal=first_principal,
                environment_public_id=first_environment.public_id,
                resource="payouts",
                limit=1,
                after=first.next_cursor,
                cursor_secret="m8-cursor-secret",
            )
        assert rebound_cursor.value.code == "INVALID_CURSOR"
    engine.dispose()

    assert second_environment.public_id != first_environment.public_id
