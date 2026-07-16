import uuid

import psycopg
import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id, new_uuid
from relaypay.payments.models import Customer, PaymentIntent
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

RELAYPAY_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"


def _create_org_customer(factory) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    organisation_id = new_uuid()
    customer_id = new_uuid()
    with factory() as session, session.begin():
        session.add(
            Organisation(
                id=organisation_id,
                public_id=new_public_id("org"),
                name="Tenant boundary organisation",
                status="ACTIVE",
            )
        )
        session.add(
            Customer(
                id=customer_id,
                public_id=new_public_id("cus"),
                organisation_id=organisation_id,
                merchant_customer_reference=f"customer-{customer_id.hex}",
            )
        )
    return organisation_id, customer_id


def test_composite_foreign_key_rejects_cross_tenant_payment() -> None:
    engine = build_engine(RELAYPAY_URL, application_name="relaypay-tenant-tests")
    factory = build_session_factory(engine)
    first_org_id, first_customer_id = _create_org_customer(factory)
    second_org_id, _ = _create_org_customer(factory)

    with pytest.raises(IntegrityError), factory() as session, session.begin():
        session.add(
            PaymentIntent(
                public_id=new_public_id("pay"),
                organisation_id=second_org_id,
                customer_id=first_customer_id,
                merchant_reference=f"cross-tenant-{first_org_id.hex}",
                amount=100,
                currency="INR",
            )
        )
    engine.dispose()


@pytest.mark.parametrize(
    ("username", "password", "database"),
    [
        ("provider_app", "provider_app_dev", "relaypay"),
        ("relaypay_app", "relaypay_app_dev", "provider"),
    ],
)
def test_application_roles_cannot_connect_to_the_other_database(
    username: str, password: str, database: str
) -> None:
    with pytest.raises(psycopg.OperationalError, match="permission denied for database"):
        psycopg.connect(
            host="localhost",
            port=55432,
            user=username,
            password=password,
            dbname=database,
            connect_timeout=3,
        )


def test_relaypay_application_role_has_no_receiver_schema_access() -> None:
    with (
        psycopg.connect(
            "postgresql://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT has_schema_privilege(current_user, 'receiver', 'USAGE')")
        assert cursor.fetchone() == (False,)
