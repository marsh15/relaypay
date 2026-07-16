import hashlib
import json
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from relaypay.contracts import EmptyCommand, PaymentIntentCreate, RefundCreate
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.idempotency import Fingerprint, build_fingerprint, canonical_json_bytes, digest_secret
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
from relaypay.ledger.service import post_capture_journal
from relaypay.payments.models import Authorization, Capture, Customer, PaymentIntent, Refund
from relaypay.payments.service import (
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
    initiate_refund,
)
from relaypay.provider_operations.models import IdempotencyRecord, ProviderOperation
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
KEY_PEPPER = "payment-command-test-pepper"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(DATABASE_URL, application_name="payment-command-tests")
    session_factory = build_session_factory(engine)
    yield session_factory
    engine.dispose()


@pytest.fixture
def merchant(factory: sessionmaker[Session]) -> tuple[uuid.UUID, Customer]:
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="Payment command tests", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation.id,
            merchant_customer_reference=f"customer-{uuid.uuid4().hex}",
            display_name="Synthetic customer",
        )
        session.add(customer)
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
    return organisation.id, customer


def _payment_payload(
    customer: Customer, merchant_reference: str, amount: int = 100_000
) -> PaymentIntentCreate:
    return PaymentIntentCreate(
        customer_id=customer.public_id,
        merchant_reference=merchant_reference,
        amount=amount,
        currency="INR",
    )


def _payment_fingerprint(payload: PaymentIntentCreate) -> Fingerprint:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template="/payment_intents",
        path_params={},
        body=payload,
    )


def _create_payment(
    factory: sessionmaker[Session],
    organisation_id: uuid.UUID,
    customer: Customer,
    *,
    amount: int = 100_000,
) -> str:
    payload = _payment_payload(customer, f"order-{uuid.uuid4().hex}", amount)
    result = create_payment_intent(
        factory,
        organisation_id=organisation_id,
        payload=payload,
        idempotency_key=f"payment-{uuid.uuid4().hex}",
        fingerprint=_payment_fingerprint(payload),
        key_pepper=KEY_PEPPER,
    )
    return str(json.loads(result.body)["id"])


def _command_fingerprint(
    payment_id: str, suffix: str, body: EmptyCommand | RefundCreate
) -> Fingerprint:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template=f"/payment_intents/{{payment_intent_id}}/{suffix}",
        path_params={"payment_intent_id": payment_id},
        body=body,
    )


def _finalize_for_test(
    factory: sessionmaker[Session], operation_public_id: str, *, financial: bool
) -> None:
    now = datetime.now(UTC)
    response = canonical_json_bytes({"status": "SUCCEEDED", "operationId": operation_public_id})
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation)
            .where(ProviderOperation.public_id == operation_public_id)
            .with_for_update()
        )
        assert operation is not None
        operation.status = "SUCCEEDED"
        operation.terminal_http_status = 200
        operation.terminal_response_headers = {"Content-Type": "application/json"}
        operation.terminal_response_bytes = response
        operation.terminal_response_sha256 = hashlib.sha256(response).digest()
        operation.finalized_at = now
        if operation.resource_type == "AUTHORIZATION":
            authorization = session.get(Authorization, operation.resource_id)
            assert authorization is not None
            authorization.status = "SUCCEEDED"
            authorization.authorized_at = now
        elif operation.resource_type == "CAPTURE":
            capture = session.get(Capture, operation.resource_id)
            assert capture is not None
            assert financial
            journal = post_capture_journal(
                session,
                organisation_id=operation.organisation_id,
                provider_operation_id=operation.id,
                capture_id=capture.id,
                amount=capture.amount,
            )
            capture.status = "SUCCEEDED"
            capture.captured_at = now
            capture.journal_id = journal.journal_id
        for record in session.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.provider_operation_id == operation.id)
        ):
            record.is_terminal = True
            record.http_status = 200
            record.response_headers = {"Content-Type": "application/json"}
            record.response_bytes = response
            record.response_sha256 = hashlib.sha256(response).digest()
            record.finalized_at = now


def test_payment_creation_replays_exact_bytes_and_rejects_conflicts(
    factory: sessionmaker[Session], merchant: tuple[uuid.UUID, Customer]
) -> None:
    organisation_id, customer = merchant
    payload = _payment_payload(customer, f"order-{uuid.uuid4().hex}")
    fingerprint = _payment_fingerprint(payload)
    key = f"payment-key-{uuid.uuid4().hex}"

    created = create_payment_intent(
        factory,
        organisation_id=organisation_id,
        payload=payload,
        idempotency_key=key,
        fingerprint=fingerprint,
        key_pepper=KEY_PEPPER,
    )
    replayed = create_payment_intent(
        factory,
        organisation_id=organisation_id,
        payload=payload,
        idempotency_key=key,
        fingerprint=fingerprint,
        key_pepper=KEY_PEPPER,
    )
    assert replayed.status_code == created.status_code == 201
    assert replayed.body == created.body
    assert replayed.headers["Idempotency-Replayed"] == "true"

    changed = payload.model_copy(update={"amount": payload.amount + 1})
    with pytest.raises(RelayPayError) as reused:
        create_payment_intent(
            factory,
            organisation_id=organisation_id,
            payload=changed,
            idempotency_key=key,
            fingerprint=_payment_fingerprint(changed),
            key_pepper=KEY_PEPPER,
        )
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    losing_key = f"different-key-{uuid.uuid4().hex}"
    with pytest.raises(RelayPayError) as merchant_conflict:
        create_payment_intent(
            factory,
            organisation_id=organisation_id,
            payload=payload,
            idempotency_key=losing_key,
            fingerprint=fingerprint,
            key_pepper=KEY_PEPPER,
        )
    assert merchant_conflict.value.code == "MERCHANT_REFERENCE_CONFLICT"
    with factory() as session, session.begin():
        losing_record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.organisation_id == organisation_id,
                IdempotencyRecord.key_digest == digest_secret(losing_key, KEY_PEPPER),
            )
        )
        assert losing_record is None


def test_same_key_payment_creation_race_converges_on_one_response(
    factory: sessionmaker[Session], merchant: tuple[uuid.UUID, Customer]
) -> None:
    organisation_id, customer = merchant
    payload = _payment_payload(customer, f"race-order-{uuid.uuid4().hex}")
    fingerprint = _payment_fingerprint(payload)
    key = f"same-key-race-{uuid.uuid4().hex}"
    barrier = threading.Barrier(2)
    results: list[tuple[int, bytes]] = []
    result_lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        result = create_payment_intent(
            factory,
            organisation_id=organisation_id,
            payload=payload,
            idempotency_key=key,
            fingerprint=fingerprint,
            key_pepper=KEY_PEPPER,
        )
        with result_lock:
            results.append((result.status_code, result.body))

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 2
    assert results[0] == results[1]
    with factory() as session, session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(PaymentIntent)
                .where(
                    PaymentIntent.organisation_id == organisation_id,
                    PaymentIntent.merchant_reference == payload.merchant_reference,
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.organisation_id == organisation_id,
                    IdempotencyRecord.key_digest == digest_secret(key, KEY_PEPPER),
                )
            )
            == 1
        )


def test_different_keys_same_merchant_reference_race_commits_no_losing_key(
    factory: sessionmaker[Session], merchant: tuple[uuid.UUID, Customer]
) -> None:
    organisation_id, customer = merchant
    payload = _payment_payload(customer, f"reference-race-{uuid.uuid4().hex}")
    fingerprint = _payment_fingerprint(payload)
    keys = [f"key-a-{uuid.uuid4().hex}", f"key-b-{uuid.uuid4().hex}"]
    barrier = threading.Barrier(2)
    outcomes: list[int | str] = []
    outcome_lock = threading.Lock()

    def worker(key: str) -> None:
        barrier.wait()
        try:
            value: int | str = create_payment_intent(
                factory,
                organisation_id=organisation_id,
                payload=payload,
                idempotency_key=key,
                fingerprint=fingerprint,
                key_pepper=KEY_PEPPER,
            ).status_code
        except RelayPayError as error:
            value = error.code
        with outcome_lock:
            outcomes.append(value)

    threads = [threading.Thread(target=worker, args=(key,)) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes, key=str) == sorted([201, "MERCHANT_REFERENCE_CONFLICT"], key=str)
    with factory() as session, session.begin():
        digests = [digest_secret(key, KEY_PEPPER) for key in keys]
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.organisation_id == organisation_id,
                    IdempotencyRecord.key_digest.in_(digests),
                )
            )
            == 1
        )


def test_compatible_authorization_keys_attach_to_one_singleton(
    factory: sessionmaker[Session], merchant: tuple[uuid.UUID, Customer]
) -> None:
    organisation_id, customer = merchant
    payment_id = _create_payment(factory, organisation_id, customer)
    fingerprint = _command_fingerprint(payment_id, "authorize", EmptyCommand())

    first = initiate_authorization(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key=f"authorize-a-{uuid.uuid4().hex}",
        fingerprint=fingerprint,
        key_pepper=KEY_PEPPER,
    )
    second = initiate_authorization(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key=f"authorize-b-{uuid.uuid4().hex}",
        fingerprint=fingerprint,
        key_pepper=KEY_PEPPER,
    )
    assert first.status_code == second.status_code == 202
    assert first.body == second.body
    with factory() as session, session.begin():
        payment = session.scalar(select(PaymentIntent).where(PaymentIntent.public_id == payment_id))
        assert payment is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(Authorization)
                .where(Authorization.payment_intent_id == payment.id)
            )
            == 1
        )
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.payment_intent_id == payment.id)
        )
        assert operation is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.provider_operation_id == operation.id)
            )
            == 2
        )


def test_concurrent_refunds_cannot_over_reserve(
    factory: sessionmaker[Session], merchant: tuple[uuid.UUID, Customer]
) -> None:
    organisation_id, customer = merchant
    payment_id = _create_payment(factory, organisation_id, customer, amount=100_000)
    authorize = initiate_authorization(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key=f"authorize-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_id, "authorize", EmptyCommand()),
        key_pepper=KEY_PEPPER,
    )
    authorize_operation_id = str(json.loads(authorize.body)["operationId"])
    _finalize_for_test(factory, authorize_operation_id, financial=False)
    capture = initiate_capture(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key=f"capture-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_id, "capture", EmptyCommand()),
        key_pepper=KEY_PEPPER,
    )
    capture_operation_id = str(json.loads(capture.body)["operationId"])
    _finalize_for_test(factory, capture_operation_id, financial=True)

    barrier = threading.Barrier(2)
    outcomes: list[int | str] = []
    outcome_lock = threading.Lock()

    def refund_worker(label: str) -> None:
        payload = RefundCreate(amount=70_000, currency="INR")
        barrier.wait()
        try:
            result = initiate_refund(
                factory,
                organisation_id=organisation_id,
                payment_public_id=payment_id,
                payload=payload,
                idempotency_key=f"refund-{label}-{uuid.uuid4().hex}",
                fingerprint=_command_fingerprint(payment_id, "refunds", payload),
                key_pepper=KEY_PEPPER,
            )
            value: int | str = result.status_code
        except RelayPayError as error:
            value = error.code
        with outcome_lock:
            outcomes.append(value)

    threads = [
        threading.Thread(target=refund_worker, args=("a",)),
        threading.Thread(target=refund_worker, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes, key=str) == sorted([202, "REFUND_AMOUNT_EXCEEDS_AVAILABLE"], key=str)
    with factory() as session, session.begin():
        payment = session.scalar(select(PaymentIntent).where(PaymentIntent.public_id == payment_id))
        assert payment is not None
        reserved = session.scalar(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.payment_intent_id == payment.id,
                Refund.status.in_(("PROCESSING", "REQUIRES_REVIEW", "SUCCEEDED")),
            )
        )
        assert reserved == 70_000
