import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx2
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.config import Settings
from relaypay.contracts import EmptyCommand, PaymentIntentCreate
from relaypay.demo_scenarios.models import ScenarioRun
from relaypay.errors import RelayPayError, not_found
from relaypay.event_delivery.delivery import WebhookTransport, claim_delivery, deliver_claim
from relaypay.event_delivery.materializer import materialize_deliveries
from relaypay.event_delivery.models import EventRecipient, MerchantEvent, WebhookDelivery
from relaypay.idempotency import Fingerprint, build_fingerprint
from relaypay.ids import new_public_id
from relaypay.ledger.models import Journal, Posting
from relaypay.payments.models import Capture, Customer, PaymentIntent
from relaypay.payments.service import (
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
)
from relaypay.provider_operations.models import IdempotencyRecord, ProviderOperation
from relaypay.provider_operations.recovery import claim_specific_operation, recover_claim
from relaypay.provider_operations.service import ProviderTransport, dispatch_operation


class ScenarioFaultController(Protocol):
    def lose_next_response(self, stable_key: str) -> None: ...

    def effect_count(self, stable_key: str) -> int: ...


class HTTPScenarioFaultController:
    def __init__(self, settings: Settings, *, timeout_seconds: float = 2.0) -> None:
        self._base_url = settings.PROVIDER_BASE_URL.rstrip("/")
        self._account_id = settings.PROVIDER_ACCOUNT_ID
        self._secret = settings.PROVIDER_CONTROL_SECRET.get_secret_value()
        self._timeout = timeout_seconds

    def lose_next_response(self, stable_key: str) -> None:
        response = httpx2.post(
            f"{self._base_url}/control/faults",
            headers={"X-Provider-Control": self._secret},
            json={
                "accountId": self._account_id,
                "stableKey": stable_key,
                "faultType": "LOSE_RESPONSE",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()

    def effect_count(self, stable_key: str) -> int:
        response = httpx2.get(
            f"{self._base_url}/control/effects/{stable_key}/proof",
            headers={"X-Provider-Control": self._secret},
            params={"account_id": self._account_id},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return int(response.json()["effectCount"])


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario_run_id: str
    status: str
    correlation_id: str
    payment_intent_id: str | None
    steps: list[dict[str, object]]
    assertions: dict[str, object]
    safe_error_code: str | None


def _command_fingerprint(payment_id: str, action: str) -> Fingerprint:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template=f"/payment_intents/{{payment_intent_id}}/{action}",
        path_params={"payment_intent_id": payment_id},
        body=EmptyCommand(),
    )


def _serialize(run: ScenarioRun, payment_id: str | None = None) -> ScenarioResult:
    return ScenarioResult(
        scenario_run_id=run.public_id,
        status=run.status,
        correlation_id=run.correlation_id,
        payment_intent_id=payment_id,
        steps=run.steps,
        assertions=run.assertions,
        safe_error_code=run.safe_error_code,
    )


def read_scenario_run(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    scenario_run_id: str,
) -> ScenarioResult:
    with factory() as session, session.begin():
        row = session.execute(
            select(ScenarioRun, PaymentIntent.public_id)
            .outerjoin(PaymentIntent, PaymentIntent.id == ScenarioRun.payment_intent_id)
            .where(
                ScenarioRun.organisation_id == organisation_id,
                ScenarioRun.public_id == scenario_run_id,
            )
        ).one_or_none()
        if row is None:
            raise not_found("Scenario run")
        return _serialize(row[0], row[1])


def run_lost_capture_scenario(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    settings: Settings,
    provider_transport: ProviderTransport,
    fault_controller: ScenarioFaultController,
    webhook_transport: WebhookTransport,
) -> ScenarioResult:
    started_at = datetime.now(UTC)
    run_public_id = new_public_id("scn")
    correlation_id = f"scenario:{run_public_id}"
    with factory() as session, session.begin():
        run = ScenarioRun(
            public_id=run_public_id,
            organisation_id=organisation_id,
            scenario_type="LOST_CAPTURE_RESPONSE",
            status="RUNNING",
            correlation_id=correlation_id,
            steps=[{"key": "setup", "label": "Setup", "status": "RUNNING"}],
            assertions={},
            started_at=started_at,
        )
        session.add(run)
    try:
        result = _execute_lost_capture(
            factory,
            organisation_id=organisation_id,
            settings=settings,
            provider_transport=provider_transport,
            fault_controller=fault_controller,
            webhook_transport=webhook_transport,
        )
        with factory() as session, session.begin():
            current_run = session.scalar(
                select(ScenarioRun).where(ScenarioRun.public_id == run_public_id).with_for_update()
            )
            if current_run is None:
                raise RuntimeError("scenario run disappeared")
            payment = session.scalar(
                select(PaymentIntent).where(
                    PaymentIntent.organisation_id == organisation_id,
                    PaymentIntent.public_id == result["paymentIntentId"],
                )
            )
            if payment is None:
                raise RuntimeError("scenario payment disappeared")
            current_run.payment_intent_id = payment.id
            current_run.status = "SUCCEEDED"
            current_run.steps = cast(list[dict[str, object]], result["steps"])
            current_run.assertions = cast(dict[str, object], result["assertions"])
            current_run.completed_at = datetime.now(UTC)
        return read_scenario_run(
            factory, organisation_id=organisation_id, scenario_run_id=run_public_id
        )
    except Exception as exc:
        with factory() as session, session.begin():
            current_run = session.scalar(
                select(ScenarioRun).where(ScenarioRun.public_id == run_public_id).with_for_update()
            )
            if current_run is not None:
                current_run.status = "NEEDS_INSPECTION"
                current_run.safe_error_code = (
                    exc.code if isinstance(exc, RelayPayError) else "SCENARIO_EXECUTION_ERROR"
                )
                current_run.completed_at = datetime.now(UTC)
        raise


def _execute_lost_capture(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    settings: Settings,
    provider_transport: ProviderTransport,
    fault_controller: ScenarioFaultController,
    webhook_transport: WebhookTransport,
) -> dict[str, object]:
    with factory() as session, session.begin():
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation_id,
            merchant_customer_reference=f"scenario-{uuid.uuid4().hex}",
            display_name="Synthetic lost-response customer",
        )
        session.add(customer)
        session.flush()
        customer_id = customer.public_id
    payment_payload = PaymentIntentCreate(
        customer_id=customer_id,
        merchant_reference=f"scenario-order-{uuid.uuid4().hex}",
        amount=125_000,
        currency="INR",
    )
    payment_result = create_payment_intent(
        factory,
        organisation_id=organisation_id,
        payload=payment_payload,
        idempotency_key=f"scenario-payment-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents",
            path_params={},
            body=payment_payload,
        ),
        key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
    )
    payment_id = str(json.loads(payment_result.body)["id"])
    auth = initiate_authorization(
        factory,
        organisation_id=organisation_id,
        payment_public_id=payment_id,
        idempotency_key=f"scenario-auth-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_id, "authorize"),
        key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
    )
    auth_operation_id = str(json.loads(auth.body)["operationId"])
    dispatch_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=auth_operation_id,
        provider_account_id=settings.PROVIDER_ACCOUNT_ID,
        provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
        transport=provider_transport,
    )
    capture_operation_ids: list[str] = []
    for suffix in ("a", "b"):
        capture = initiate_capture(
            factory,
            organisation_id=organisation_id,
            payment_public_id=payment_id,
            idempotency_key=f"scenario-capture-{suffix}-{uuid.uuid4().hex}",
            fingerprint=_command_fingerprint(payment_id, "capture"),
            key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
        )
        capture_operation_ids.append(str(json.loads(capture.body)["operationId"]))
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(
                ProviderOperation.organisation_id == organisation_id,
                ProviderOperation.public_id == capture_operation_ids[0],
            )
        )
        if operation is None:
            raise RuntimeError("capture operation is missing")
        stable_key = operation.stable_provider_key
    fault_controller.lose_next_response(stable_key)
    dispatch_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=capture_operation_ids[0],
        provider_account_id=settings.PROVIDER_ACCOUNT_ID,
        provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
        transport=provider_transport,
    )
    claim = claim_specific_operation(
        factory,
        organisation_id=organisation_id,
        operation_public_id=capture_operation_ids[0],
    )
    recover_claim(
        factory,
        claim=claim,
        provider_account_id=settings.PROVIDER_ACCOUNT_ID,
        provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
        transport=provider_transport,
    )
    materialize_deliveries(factory, organisation_id=organisation_id)
    delivery_claim = claim_delivery(factory, organisation_id=organisation_id)
    if delivery_claim is None:
        raise RuntimeError("scenario delivery was not materialized")
    if not deliver_claim(
        factory,
        delivery_claim,
        encryption_key=settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
        transport=webhook_transport,
    ):
        raise RuntimeError("scenario delivery lease was lost")
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation).where(
                ProviderOperation.organisation_id == organisation_id,
                ProviderOperation.public_id == capture_operation_ids[0],
            )
        )
        if operation is None:
            raise RuntimeError("capture operation disappeared")
        event = session.scalar(
            select(MerchantEvent).where(MerchantEvent.provider_operation_id == operation.id)
        )
        journal = session.scalar(
            select(Journal).where(Journal.provider_operation_id == operation.id)
        )
        if event is None or journal is None:
            raise RuntimeError("scenario evidence is incomplete")
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
        assertions: dict[str, object] = {
            "providerEffects": fault_controller.effect_count(stable_key),
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
            "attachedKeys": session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.provider_operation_id == operation.id)
            ),
            "stableReplay": len(set(capture_operation_ids)) == 1,
            "eventSha256": event.event_sha256.hex(),
            "responseSha256": operation.terminal_response_sha256.hex()
            if operation.terminal_response_sha256
            else None,
        }
    expected_counts = (1, 1, 1, 1, 1, 2)
    observed_counts = tuple(
        assertions[key]
        for key in (
            "providerEffects",
            "captures",
            "journals",
            "events",
            "recipients",
            "attachedKeys",
        )
    )
    if observed_counts != expected_counts or not assertions["ledgerBalanced"]:
        raise RuntimeError("lost-response scenario invariants did not hold")
    steps = [
        {"key": "setup", "label": "Setup", "status": "COMPLETE"},
        {"key": "authorized", "label": "Authorized", "status": "COMPLETE"},
        {"key": "capture-sent", "label": "Capture sent", "status": "COMPLETE"},
        {"key": "response-lost", "label": "Response lost", "status": "COMPLETE"},
        {"key": "lookup", "label": "Status lookup", "status": "COMPLETE"},
        {"key": "finalized", "label": "Finalized", "status": "COMPLETE"},
        {"key": "delivered", "label": "Delivered", "status": "COMPLETE"},
    ]
    return {"paymentIntentId": payment_id, "steps": steps, "assertions": assertions}
