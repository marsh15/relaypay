import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.contracts import EmptyCommand, PaymentIntentCreate, RefundCreate
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.models import MerchantEvent
from relaypay.idempotency import build_fingerprint, canonical_json_bytes
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.payments.models import Authorization, Customer, Refund
from relaypay.payments.service import (
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
    initiate_refund,
    read_payment,
)
from relaypay.provider_operations.finalizer import record_apply_failure
from relaypay.provider_operations.models import (
    IdempotencyRecord,
    ProviderAttempt,
    ProviderOperation,
)
from relaypay.provider_operations.recovery import (
    claim_due_operations,
    claim_specific_operation,
    recover_claim,
)
from relaypay.provider_operations.service import (
    ProviderTransport,
    classify_and_record_lookup,
    dispatch_operation,
)
from relaypay.provider_operations.service_types import ProviderObservation
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"
ACCOUNT_ID = "acct_relaypay_demo"
SIGNING_SECRET = "provider-signing-secret-for-week3-tests"
PEPPER = "week3-idempotency-pepper"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = build_engine(DATABASE_URL, application_name="week3-recovery-tests")
    session_factory = build_session_factory(engine)
    yield session_factory
    engine.dispose()


def _signed_reply(request_bytes: bytes, *, outcome: str = "SUCCEEDED") -> ProviderObservation:
    request = json.loads(request_bytes)
    body = canonical_json_bytes(
        {
            **request,
            "declineCode": "DO_NOT_HONOR" if outcome == "DECLINED" else None,
            "effectId": f"effect-{uuid.uuid4().hex}",
            "outcome": outcome,
        }
    )
    signature = hmac.new(SIGNING_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return ProviderObservation(200, body, {"X-Provider-Signature": signature})


class DeterministicTransport(ProviderTransport):
    def __init__(
        self,
        *,
        outcome: str = "SUCCEEDED",
        lose_mutation_response: bool = False,
        unsigned_mutation: bool = False,
        factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.outcome = outcome
        self.lose_mutation_response = lose_mutation_response
        self.unsigned_mutation = unsigned_mutation
        self.factory = factory
        self.mutation_count = 0
        self.lookup_count = 0
        self.request_bytes: bytes | None = None
        self.lookup_observed_no_open_transaction = False

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        self.mutation_count += 1
        self.request_bytes = request_bytes
        if self.lose_mutation_response:
            return ProviderObservation(599, b"", {})
        reply = _signed_reply(request_bytes, outcome=self.outcome)
        if self.unsigned_mutation:
            return ProviderObservation(reply.status_code, reply.body, {})
        return reply

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        assert account_id == ACCOUNT_ID
        assert self.request_bytes is not None
        assert json.loads(self.request_bytes)["stableKey"] == stable_key
        self.lookup_count += 1
        if self.factory is not None:
            with self.factory() as session, session.begin():
                operation = session.scalar(
                    select(ProviderOperation)
                    .where(ProviderOperation.stable_provider_key == stable_key)
                    .with_for_update(nowait=True)
                )
                self.lookup_observed_no_open_transaction = operation is not None
        return _signed_reply(self.request_bytes, outcome=self.outcome)


class IndeterminateLookupTransport(DeterministicTransport):
    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        assert account_id == ACCOUNT_ID
        assert stable_key
        self.lookup_count += 1
        return ProviderObservation(503, b"temporary provider failure", {})


def _new_payment(factory: sessionmaker[Session], *, amount: int = 100_000) -> tuple[uuid.UUID, str]:
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="Week 3 tests", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation.id,
            merchant_customer_reference=f"customer-{uuid.uuid4().hex}",
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
        session.flush()
    payload = PaymentIntentCreate(
        customer_id=customer.public_id,
        merchant_reference=f"order-{uuid.uuid4().hex}",
        amount=amount,
        currency="INR",
    )
    result = create_payment_intent(
        factory,
        organisation_id=organisation.id,
        payload=payload,
        idempotency_key=f"payment-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents",
            path_params={},
            body=payload,
        ),
        key_pepper=PEPPER,
    )
    return organisation.id, str(json.loads(result.body)["id"])


def _authorize(
    factory: sessionmaker[Session],
    organisation_id: uuid.UUID,
    payment_id: str,
    *,
    key: str | None = None,
) -> str:
    command_key = key or f"authorize-{uuid.uuid4().hex}"
    result = initiate_authorization(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key=command_key,
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents/{payment_intent_id}/authorize",
            path_params={"payment_intent_id": payment_id},
            body=EmptyCommand(),
        ),
        key_pepper=PEPPER,
    )
    return str(json.loads(result.body)["operationId"])


def _dispatch(
    factory: sessionmaker[Session],
    organisation_id: uuid.UUID,
    operation_id: str,
    transport: ProviderTransport,
) -> None:
    dispatch_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_id,
        provider_account_id=ACCOUNT_ID,
        provider_signing_secret=SIGNING_SECRET,
        transport=transport,
    )


def _succeed_authorization(
    factory: sessionmaker[Session], organisation_id: uuid.UUID, payment_id: str
) -> str:
    operation_id = _authorize(factory, organisation_id, payment_id)
    _dispatch(factory, organisation_id, operation_id, DeterministicTransport())
    return operation_id


def test_authorization_success_has_one_event_zero_journals_and_stable_attached_results(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    operation_id = _authorize(factory, organisation_id, payment_id, key="auth-key-a")
    assert _authorize(factory, organisation_id, payment_id, key="auth-key-b") == operation_id

    _dispatch(factory, organisation_id, operation_id, DeterministicTransport())

    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None and operation.status == "SUCCEEDED"
        records = session.scalars(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.provider_operation_id == operation.id)
            .order_by(IdempotencyRecord.id)
        ).all()
        assert len(records) == 2
        assert all(record.is_terminal for record in records)
        assert {record.response_bytes for record in records} == {operation.terminal_response_bytes}
        assert (
            session.scalar(
                select(func.count())
                .select_from(Journal)
                .where(Journal.provider_operation_id == operation.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(MerchantEvent)
                .where(MerchantEvent.provider_operation_id == operation.id)
            )
            == 1
        )
        event = session.scalar(
            select(MerchantEvent).where(MerchantEvent.provider_operation_id == operation.id)
        )
        assert event is not None
        event_id = event.id
        original_bytes = event.event_bytes

    with pytest.raises(DBAPIError), factory() as session, session.begin():
        event = session.get(MerchantEvent, event_id)
        assert event is not None
        event.event_bytes = b"tampered"
    with factory() as session, session.begin():
        event = session.get(MerchantEvent, event_id)
        assert event is not None and event.event_bytes == original_bytes


def test_inline_and_lookup_race_produces_one_capture_journal_event_and_transition(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    _succeed_authorization(factory, organisation_id, payment_id)
    capture = initiate_capture(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key="capture-key-a",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents/{payment_intent_id}/capture",
            path_params={"payment_intent_id": payment_id},
            body=EmptyCommand(),
        ),
        key_pepper=PEPPER,
    )
    capture_operation_id = str(json.loads(capture.body)["operationId"])
    second = initiate_capture(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key="capture-key-b",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents/{payment_intent_id}/capture",
            path_params={"payment_intent_id": payment_id},
            body=EmptyCommand(),
        ),
        key_pepper=PEPPER,
    )
    assert json.loads(second.body)["operationId"] == capture_operation_id
    transport = DeterministicTransport(lose_mutation_response=True)
    _dispatch(factory, organisation_id, capture_operation_id, transport)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_dispatch, factory, organisation_id, capture_operation_id, transport)
            for _ in range(2)
        ]
        for future in futures:
            future.result()

    assert transport.mutation_count == 1
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == capture_operation_id)
        )
        assert operation is not None and operation.status == "SUCCEEDED"
        journal = session.scalar(
            select(Journal).where(Journal.provider_operation_id == operation.id)
        )
        assert journal is not None
        postings = session.scalars(select(Posting).where(Posting.journal_id == journal.id)).all()
        assert len(postings) == 2
        assert sum(p.amount for p in postings if p.side == "DEBIT") == sum(
            p.amount for p in postings if p.side == "CREDIT"
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(MerchantEvent)
                .where(MerchantEvent.provider_operation_id == operation.id)
            )
            == 1
        )
        records = session.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.provider_operation_id == operation.id)
        ).all()
        assert len(records) == 2
        assert {record.response_bytes for record in records} == {operation.terminal_response_bytes}


def test_invalid_evidence_enters_review_and_admin_can_only_retry_status_lookup(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    operation_id = _authorize(factory, organisation_id, payment_id)
    invalid_transport = DeterministicTransport(unsigned_mutation=True)
    _dispatch(factory, organisation_id, operation_id, invalid_transport)

    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None
        assert (operation.status, operation.review_reason) == (
            "REQUIRES_REVIEW",
            "INVALID_EVIDENCE",
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(MerchantEvent)
                .where(MerchantEvent.provider_operation_id == operation.id)
            )
            == 0
        )

    claim = claim_specific_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_id,
    )
    recover_claim(
        factory,
        claim=claim,
        provider_account_id=ACCOUNT_ID,
        provider_signing_secret=SIGNING_SECRET,
        transport=invalid_transport,
        actor_type="ADMIN_LOOKUP",
    )
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None and operation.status == "SUCCEEDED"


def test_expired_recovery_lease_is_reclaimed_and_network_holds_no_database_transaction(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    operation_id = _authorize(factory, organisation_id, payment_id)
    transport = DeterministicTransport(lose_mutation_response=True, factory=factory)
    _dispatch(factory, organisation_id, operation_id, transport)

    first_claims = claim_due_operations(factory)
    first = next(claim for claim in first_claims if claim.operation_public_id == operation_id)
    assert all(claim.operation_public_id != operation_id for claim in claim_due_operations(factory))
    with factory() as session, session.begin():
        operation = session.get(ProviderOperation, first.operation_id)
        assert operation is not None
        operation.lookup_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    reclaimed = next(
        claim
        for claim in claim_due_operations(factory)
        if claim.operation_public_id == operation_id
    )
    assert reclaimed.lease_token != first.lease_token

    recover_claim(
        factory,
        claim=reclaimed,
        provider_account_id=ACCOUNT_ID,
        provider_signing_secret=SIGNING_SECRET,
        transport=transport,
    )

    assert transport.lookup_observed_no_open_transaction
    with factory() as session, session.begin():
        operation = session.get(ProviderOperation, reclaimed.operation_id)
        assert operation is not None and operation.status == "SUCCEEDED"


def test_crash_after_lookup_response_persistence_recovers_without_mutation_retry(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    operation_id = _authorize(factory, organisation_id, payment_id)
    transport = DeterministicTransport(lose_mutation_response=True)
    _dispatch(factory, organisation_id, operation_id, transport)
    claim = claim_specific_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_id,
    )
    observation = transport.lookup(account_id=ACCOUNT_ID, stable_key=claim.stable_key)
    persisted_outcome = classify_and_record_lookup(
        factory,
        operation_id=claim.operation_id,
        lease_token=claim.lease_token,
        provider_account_id=ACCOUNT_ID,
        provider_signing_secret=SIGNING_SECRET,
        observation=observation,
    )
    assert persisted_outcome is not None

    with factory() as session, session.begin():
        operation = session.get(ProviderOperation, claim.operation_id)
        assert operation is not None and operation.status == "PROCESSING"
        attempt = session.get(ProviderAttempt, persisted_outcome.attempt_id)
        assert attempt is not None
        assert (attempt.state, attempt.classification) == (
            "RESPONSE_RECEIVED",
            "VERIFIED_SUCCESS",
        )

    replacement_claim = claim_specific_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=operation_id,
    )
    recover_claim(
        factory,
        claim=replacement_claim,
        provider_account_id=ACCOUNT_ID,
        provider_signing_secret=SIGNING_SECRET,
        transport=transport,
    )
    assert transport.mutation_count == 1
    with factory() as session, session.begin():
        operation = session.get(ProviderOperation, claim.operation_id)
        assert operation is not None and operation.status == "SUCCEEDED"


def test_verified_failed_refund_releases_reservation_without_journal_or_event(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    _succeed_authorization(factory, organisation_id, payment_id)
    capture = initiate_capture(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key=f"capture-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents/{payment_intent_id}/capture",
            path_params={"payment_intent_id": payment_id},
            body=EmptyCommand(),
        ),
        key_pepper=PEPPER,
    )
    _dispatch(
        factory,
        organisation_id,
        str(json.loads(capture.body)["operationId"]),
        DeterministicTransport(),
    )
    refund_payload = RefundCreate(
        amount=40_000,
        currency="INR",
        merchant_refund_reference=f"refund-{uuid.uuid4().hex}",
    )
    refund = initiate_refund(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        payload=refund_payload,
        idempotency_key=f"refund-key-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents/{payment_intent_id}/refunds",
            path_params={"payment_intent_id": payment_id},
            body=refund_payload,
        ),
        key_pepper=PEPPER,
    )
    refund_operation_id = str(json.loads(refund.body)["operationId"])
    _dispatch(
        factory,
        organisation_id,
        refund_operation_id,
        DeterministicTransport(outcome="DECLINED"),
    )

    payment = json.loads(
        read_payment(
            factory,
            organisation_id=organisation_id,
            payment_public_id=payment_id,
        ).body
    )
    assert payment["refundableAmount"] == 100_000
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == refund_operation_id)
        )
        assert operation is not None and operation.status == "FAILED"
        refund_row = session.scalar(
            select(Refund).where(Refund.provider_operation_id == operation.id)
        )
        assert refund_row is not None and refund_row.failure_code == "DO_NOT_HONOR"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Journal)
                .where(Journal.provider_operation_id == operation.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(MerchantEvent)
                .where(MerchantEvent.provider_operation_id == operation.id)
            )
            == 0
        )


def test_repeated_local_apply_failures_move_operation_to_review(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    operation_id = _authorize(factory, organisation_id, payment_id)
    transport = DeterministicTransport(lose_mutation_response=True)
    _dispatch(factory, organisation_id, operation_id, transport)
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None
        attempt = session.scalar(
            select(ProviderAttempt).where(
                ProviderAttempt.provider_operation_id == operation.id,
                ProviderAttempt.attempt_kind == "MUTATION",
            )
        )
        assert attempt is not None
        operation_db_id = operation.id
        attempt_id = attempt.id

    for index in range(3):
        assert (
            record_apply_failure(
                factory,
                operation_id=operation_db_id,
                evidence_attempt_id=attempt_id,
                correlation_id=f"apply-failure-{index}",
            )
            == index + 1
        )
    with factory() as session, session.begin():
        operation = session.get(ProviderOperation, operation_db_id)
        authorization = session.scalar(
            select(Authorization).where(Authorization.provider_operation_id == operation_db_id)
        )
        assert operation is not None and authorization is not None
        assert (operation.status, operation.review_reason, operation.apply_failure_count) == (
            "REQUIRES_REVIEW",
            "APPLY_FAILURE",
            3,
        )
        assert authorization.status == "REQUIRES_REVIEW"


def test_lookup_remaining_indeterminate_moves_to_review_after_bounded_attempts(
    factory: sessionmaker[Session],
) -> None:
    organisation_id, payment_id = _new_payment(factory)
    operation_id = _authorize(factory, organisation_id, payment_id)
    transport = IndeterminateLookupTransport(lose_mutation_response=True)
    _dispatch(factory, organisation_id, operation_id, transport)

    for _ in range(5):
        claim = claim_specific_operation(
            factory,
            organisation_id=organisation_id,
            operation_public_id=operation_id,
        )
        recover_claim(
            factory,
            claim=claim,
            provider_account_id=ACCOUNT_ID,
            provider_signing_secret=SIGNING_SECRET,
            transport=transport,
        )

    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(ProviderOperation.public_id == operation_id)
        )
        assert operation is not None
        assert (operation.status, operation.review_reason) == (
            "REQUIRES_REVIEW",
            "PROVIDER_INDETERMINATE",
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(MerchantEvent)
                .where(MerchantEvent.provider_operation_id == operation.id)
            )
            == 0
        )
