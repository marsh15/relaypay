from typing import Annotated, Literal

from fastapi import Body, FastAPI, Header
from pydantic import BaseModel, ConfigDict, Field
from relaypay.subscriptions.network import (
    DeterministicCommunicationNetwork,
    DeterministicRecurringPaymentNetwork,
)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel: Literal["EMAIL", "WHATSAPP", "IN_APP"]
    body: str = Field(min_length=1, max_length=4_000)


class PaymentRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    invoice_id: str = Field(alias="invoiceId", pattern=r"^inv_[0-9a-f]{32}$")
    amount: int = Field(gt=0)


def create_app(
    messages: DeterministicCommunicationNetwork | None = None,
    payments: DeterministicRecurringPaymentNetwork | None = None,
) -> FastAPI:
    resolved_messages = messages or DeterministicCommunicationNetwork()
    resolved_payments = payments or DeterministicRecurringPaymentNetwork()
    app = FastAPI(
        title="RelayPay synthetic recovery network",
        version="0.13.0",
        description="Synthetic data only. Never send real customer, message, or payment data.",
    )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.post("/v1/messages")
    def send_message(
        request: Annotated[MessageRequest, Body()],
        stable_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=192)],
    ) -> dict[str, object]:
        observation = resolved_messages.send(
            stable_key=stable_key, channel=request.channel, body=request.body
        )
        return {
            "status": observation.status,
            "code": observation.code,
            "effectCount": resolved_messages.effect_count,
        }

    @app.get("/v1/messages/{stable_key}")
    def lookup_message(stable_key: str) -> dict[str, object]:
        observation = resolved_messages.lookup(stable_key=stable_key)
        return {
            "status": observation.status,
            "code": observation.code,
            "effectCount": resolved_messages.effect_count,
        }

    @app.post("/v1/payment-retries")
    def retry_payment(
        request: Annotated[PaymentRetryRequest, Body()],
        stable_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=192)],
    ) -> dict[str, object]:
        observation = resolved_payments.retry(
            stable_key=stable_key, invoice_id=request.invoice_id, amount=request.amount
        )
        return {
            "status": observation.status,
            "code": observation.code,
            "effectCount": resolved_payments.effect_count,
        }

    @app.get("/v1/payment-retries/{stable_key}")
    def lookup_payment(stable_key: str) -> dict[str, object]:
        observation = resolved_payments.lookup(stable_key=stable_key)
        return {
            "status": observation.status,
            "code": observation.code,
            "effectCount": resolved_payments.effect_count,
        }

    return app
