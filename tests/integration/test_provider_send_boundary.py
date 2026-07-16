import json
import uuid
from collections.abc import Iterator

import pytest
from relaypay.contracts import EmptyCommand, PaymentIntentCreate
from relaypay.database import build_engine, build_session_factory
from relaypay.idempotency import build_fingerprint
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id
from relaypay.payments.models import Customer
from relaypay.payments.service import create_payment_intent, initiate_authorization
from relaypay.provider_operations.models import ProviderAttempt, ProviderOperation
from relaypay.provider_operations.service import (
    ProviderObservation,
    dispatch_operation,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(DATABASE_URL, application_name="send-boundary-tests")
    session_factory = build_session_factory(engine)
    yield session_factory
    engine.dispose()


def _initiated_authorization(
    factory: sessionmaker[Session],
) -> tuple[uuid.UUID, str]:
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="Send boundary tests", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation.id,
            merchant_customer_reference=f"customer-{uuid.uuid4().hex}",
        )
        session.add(customer)
        session.flush()
    payment_payload = PaymentIntentCreate(
        customer_id=customer.public_id,
        merchant_reference=f"order-{uuid.uuid4().hex}",
        amount=100_000,
        currency="INR",
    )
    payment = create_payment_intent(
        factory,
        organisation_id=organisation.id,
        payload=payment_payload,
        idempotency_key=f"payment-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents",
            path_params={},
            body=payment_payload,
        ),
        key_pepper="send-boundary-pepper",
    )
    payment_id = str(json.loads(payment.body)["id"])
    authorization = initiate_authorization(
        factory,
        organisation_id=organisation.id,
        payment_public_id=payment_id,
        idempotency_key=f"authorize-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents/{payment_intent_id}/authorize",
            path_params={"payment_intent_id": payment_id},
            body=EmptyCommand(),
        ),
        key_pepper="send-boundary-pepper",
    )
    return organisation.id, str(json.loads(authorization.body)["operationId"])


class InspectingTransport:
    def __init__(self, factory: sessionmaker[Session], operation_id: str) -> None:
        self.factory = factory
        self.operation_id = operation_id
        self.mutations = 0
        self.lookups = 0
        self.sent_was_committed = False

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        assert json.loads(request_bytes)["stableKey"].startswith("authorize:")
        self.mutations += 1
        with self.factory() as session, session.begin():
            operation = session.scalar(
                select(ProviderOperation).where(ProviderOperation.public_id == self.operation_id)
            )
            assert operation is not None
            attempt = session.scalar(
                select(ProviderAttempt).where(
                    ProviderAttempt.provider_operation_id == operation.id,
                    ProviderAttempt.attempt_kind == "MUTATION",
                )
            )
            self.sent_was_committed = (
                operation.last_sent_at is not None
                and operation.provider_request_bytes == request_bytes
                and attempt is not None
                and attempt.state == "SENT"
                and attempt.completed_at is None
            )
        return ProviderObservation(599, b"", {})

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        assert account_id == "acct_relaypay_demo"
        assert stable_key.startswith("authorize:")
        self.lookups += 1
        return ProviderObservation(200, b'{"outcome":"SUCCEEDED"}', {})


def test_sent_commits_before_http_and_recorded_send_is_status_only(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, operation_id = _initiated_authorization(factory)
    transport = InspectingTransport(factory, operation_id)

    dispatch_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_id,
        provider_account_id="acct_relaypay_demo",
        provider_signing_secret="provider-signing-secret-for-tests",
        transport=transport,
    )
    dispatch_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_id,
        provider_account_id="acct_relaypay_demo",
        provider_signing_secret="provider-signing-secret-for-tests",
        transport=transport,
    )

    assert transport.sent_was_committed
    assert transport.mutations == 1
    assert transport.lookups == 1
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None
        attempts = session.scalars(
            select(ProviderAttempt)
            .where(ProviderAttempt.provider_operation_id == operation.id)
            .order_by(ProviderAttempt.sequence)
        ).all()
        assert [(attempt.attempt_kind, attempt.state) for attempt in attempts] == [
            ("MUTATION", "RESPONSE_RECEIVED"),
            ("LOOKUP", "VALIDATION_REJECTED"),
        ]
        assert operation.status == "REQUIRES_REVIEW"
        assert operation.review_reason == "INVALID_EVIDENCE"
