import json
import uuid
from datetime import UTC, datetime

from relaypay.config import Settings, get_settings
from relaypay.contracts import EmptyCommand, PaymentIntentCreate
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.crypto import encrypt_webhook_secret
from relaypay.event_delivery.delivery import DeliveryResponse, claim_delivery, deliver_claim
from relaypay.event_delivery.materializer import materialize_deliveries
from relaypay.event_delivery.models import (
    EventRecipient,
    MerchantEvent,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEndpointVersion,
)
from relaypay.idempotency import Fingerprint, build_fingerprint
from relaypay.identity.models import Organisation
from relaypay.ids import new_public_id
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.mock_provider.models import ProviderEffect
from relaypay.mock_provider.service import (
    EffectCommand,
    apply_effect,
    configure_fault,
    lookup_effect,
)
from relaypay.payments.models import Capture, Customer
from relaypay.payments.service import (
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
)
from relaypay.provider_operations.models import IdempotencyRecord, ProviderOperation
from relaypay.provider_operations.recovery import run_recovery_batch
from relaypay.provider_operations.service import ProviderTransport, dispatch_operation
from relaypay.provider_operations.service_types import ProviderObservation
from relaypay.receiver.models import ReceivedEvent
from relaypay.receiver.service import receive_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker


class LocalProviderTransport(ProviderTransport):
    def __init__(self, factory: sessionmaker[Session], settings: Settings) -> None:
        self.factory = factory
        self.settings = settings

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        payload = json.loads(request_bytes)
        reply = apply_effect(
            self.factory,
            command=EffectCommand(
                account_id=payload["accountId"],
                stable_key=payload["stableKey"],
                operation_kind=payload["operationKind"],
                reference=payload["reference"],
                amount=payload["amount"],
                currency=payload["currency"],
            ),
            signing_secret=self.settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        reply = lookup_effect(
            self.factory,
            account_public_id=account_id,
            stable_key=stable_key,
            signing_secret=self.settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)


class LocalReceiverTransport:
    def __init__(self, factory: sessionmaker[Session], secret: str, url: str) -> None:
        self.factory = factory
        self.secret = secret
        self.url = url

    def send(self, *, url: str, body: bytes, headers: dict[str, str]) -> DeliveryResponse:
        if url != self.url:
            raise ValueError("unexpected demo receiver URL")
        with self.factory() as session, session.begin():
            receive_event(
                session,
                body=body,
                event_id=headers["X-RelayPay-Event-Id"],
                timestamp_text=headers["X-RelayPay-Timestamp"],
                signature=headers["X-RelayPay-Signature"],
                secret=self.secret,
            )
        return DeliveryResponse(200)


def _fingerprint(payment_id: str, route: str) -> Fingerprint:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template=route,
        path_params={"payment_intent_id": payment_id},
        body=EmptyCommand(),
    )


def run(settings: Settings) -> dict[str, object]:
    relay_engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="lost-response-demo"
    )
    provider_engine = build_engine(
        settings.PROVIDER_DATABASE_URL.get_secret_value(), application_name="lost-response-provider"
    )
    receiver_engine = build_engine(
        settings.RECEIVER_DATABASE_URL.get_secret_value(), application_name="lost-response-receiver"
    )
    relay = build_session_factory(relay_engine)
    provider = build_session_factory(provider_engine)
    receiver = build_session_factory(receiver_engine)
    receiver_url = f"{settings.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay"
    try:
        with relay() as session, session.begin():
            organisation = Organisation(
                public_id=new_public_id("org"), name="Lost response proof", status="ACTIVE"
            )
            session.add(organisation)
            session.flush()
            customer = Customer(
                public_id=new_public_id("cus"),
                organisation_id=organisation.id,
                merchant_customer_reference=f"demo-{uuid.uuid4().hex}",
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
            endpoint = WebhookEndpoint(
                public_id=new_public_id("wh"),
                organisation_id=organisation.id,
                name="Bundled receiver",
                status="ACTIVE",
            )
            session.add(endpoint)
            session.flush()
            session.add(
                WebhookEndpointVersion(
                    public_id=new_public_id("whv"),
                    organisation_id=organisation.id,
                    webhook_endpoint_id=endpoint.id,
                    version=1,
                    url=receiver_url,
                    encrypted_secret=encrypt_webhook_secret(
                        settings.RECEIVER_WEBHOOK_SECRET.get_secret_value(),
                        settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
                    ),
                    subscribed_event_types=["payment.captured.v1"],
                    active_from=datetime.now(UTC),
                )
            )
            session.flush()
            organisation_id = organisation.id
            customer_id = customer.public_id
        payment_payload = PaymentIntentCreate(
            customer_id=customer_id,
            merchant_reference=f"demo-order-{uuid.uuid4().hex}",
            amount=125_000,
            currency="INR",
        )
        payment_result = create_payment_intent(
            relay,
            organisation_id=organisation_id,
            payload=payment_payload,
            idempotency_key=f"payment-{uuid.uuid4().hex}",
            fingerprint=build_fingerprint(
                api_version="v1",
                method="POST",
                route_template="/payment_intents",
                path_params={},
                body=payment_payload,
            ),
            key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
        )
        payment_id = json.loads(payment_result.body)["id"]
        transport = LocalProviderTransport(provider, settings)
        auth = initiate_authorization(
            relay,
            organisation_id=organisation_id,
            payment_public_id=payment_id,
            idempotency_key=f"auth-{uuid.uuid4().hex}",
            fingerprint=_fingerprint(payment_id, "/payment_intents/{payment_intent_id}/authorize"),
            key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
        )
        auth_id = json.loads(auth.body)["operationId"]
        dispatch_operation(
            relay,
            organisation_id=organisation_id,
            operation_public_id=auth_id,
            provider_account_id=settings.PROVIDER_ACCOUNT_ID,
            provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
            transport=transport,
        )
        capture_keys = (f"capture-a-{uuid.uuid4().hex}", f"capture-b-{uuid.uuid4().hex}")
        capture_ids: list[str] = []
        for key in capture_keys:
            result = initiate_capture(
                relay,
                organisation_id=organisation_id,
                payment_public_id=payment_id,
                idempotency_key=key,
                fingerprint=_fingerprint(
                    payment_id, "/payment_intents/{payment_intent_id}/capture"
                ),
                key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
            )
            capture_ids.append(json.loads(result.body)["operationId"])
        with relay() as session, session.begin():
            operation = session.scalar(
                select(ProviderOperation).where(ProviderOperation.public_id == capture_ids[0])
            )
            assert operation is not None
            stable_key = operation.stable_provider_key
        configure_fault(
            provider,
            account_public_id=settings.PROVIDER_ACCOUNT_ID,
            stable_key=stable_key,
            fault_type="LOSE_RESPONSE",
        )
        dispatch_operation(
            relay,
            organisation_id=organisation_id,
            operation_public_id=capture_ids[0],
            provider_account_id=settings.PROVIDER_ACCOUNT_ID,
            provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
            transport=transport,
        )
        run_recovery_batch(
            relay,
            provider_account_id=settings.PROVIDER_ACCOUNT_ID,
            provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
            transport=transport,
        )
        materialize_deliveries(relay)
        claim = claim_delivery(relay, organisation_id=organisation_id)
        assert claim is not None
        deliver_claim(
            relay,
            claim,
            encryption_key=settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
            transport=LocalReceiverTransport(
                receiver,
                settings.RECEIVER_WEBHOOK_SECRET.get_secret_value(),
                receiver_url,
            ),
        )
        with relay() as session, session.begin():
            operation = session.scalar(
                select(ProviderOperation).where(ProviderOperation.public_id == capture_ids[0])
            )
            assert operation is not None
            event = session.scalar(
                select(MerchantEvent).where(MerchantEvent.provider_operation_id == operation.id)
            )
            assert event is not None
            journal = session.scalar(
                select(Journal).where(Journal.provider_operation_id == operation.id)
            )
            assert journal is not None
            debit = session.scalar(
                select(func.coalesce(func.sum(Posting.amount), 0)).where(
                    Posting.journal_id == journal.id, Posting.side == "DEBIT"
                )
            )
            credit = session.scalar(
                select(func.coalesce(func.sum(Posting.amount), 0)).where(
                    Posting.journal_id == journal.id, Posting.side == "CREDIT"
                )
            )
            proof: dict[str, object] = {
                "scenario": "capture_lost_response",
                "stableReplay": len(set(capture_ids)) == 1,
                "providerEffects": 0,
                "captures": session.scalar(
                    select(func.count())
                    .select_from(Capture)
                    .where(Capture.provider_operation_id == operation.id)
                ),
                "journals": 1,
                "ledgerBalanced": debit == credit,
                "events": session.scalar(
                    select(func.count())
                    .select_from(MerchantEvent)
                    .where(MerchantEvent.provider_operation_id == operation.id)
                ),
                "recipients": session.scalar(
                    select(func.count())
                    .select_from(EventRecipient)
                    .where(EventRecipient.merchant_event_id == event.id)
                ),
                "deliveries": session.scalar(
                    select(func.count())
                    .select_from(WebhookDelivery)
                    .where(WebhookDelivery.organisation_id == organisation_id)
                ),
                "idempotencyRecords": session.scalar(
                    select(func.count())
                    .select_from(IdempotencyRecord)
                    .where(IdempotencyRecord.provider_operation_id == operation.id)
                ),
                "eventSha256": event.event_sha256.hex(),
                "responseSha256": operation.terminal_response_sha256.hex()
                if operation.terminal_response_sha256
                else None,
            }
        with provider() as session, session.begin():
            proof["providerEffects"] = session.scalar(
                select(func.count())
                .select_from(ProviderEffect)
                .where(ProviderEffect.stable_key == stable_key)
            )
        with receiver() as session, session.begin():
            proof["receiverRows"] = session.scalar(
                select(func.count())
                .select_from(ReceivedEvent)
                .where(ReceivedEvent.event_id == event.public_id)
            )
        expected = {
            "providerEffects": 1,
            "captures": 1,
            "journals": 1,
            "events": 1,
            "recipients": 1,
            "deliveries": 1,
            "idempotencyRecords": 2,
            "receiverRows": 1,
        }
        if any(proof.get(key) != value for key, value in expected.items()):
            raise RuntimeError(f"lost-response proof failed: {proof}")
        if not proof["stableReplay"] or not proof["ledgerBalanced"]:
            raise RuntimeError(f"lost-response invariants failed: {proof}")
        return proof
    finally:
        relay_engine.dispose()
        provider_engine.dispose()
        receiver_engine.dispose()


def main() -> None:
    print(json.dumps(run(get_settings()), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
