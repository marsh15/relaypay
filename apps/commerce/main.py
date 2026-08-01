import hashlib
import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from relaypay.config import Settings, get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.mock_commerce.service import create_order, link_payment, synchronize_event
from relaypay.observability.metrics import install_asgi_metrics
from relaypay.observability.telemetry import instrument_fastapi


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_id: str = Field(alias="accountId")
    external_reference: str = Field(alias="externalReference", min_length=1, max_length=128)
    total_amount: int = Field(alias="totalAmount", gt=0)
    currency: Literal["INR"]


class PaymentLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    relaypay_payment_id: str = Field(alias="relaypayPaymentId")
    amount: int = Field(gt=0)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    engine = build_engine(
        resolved.COMMERCE_DATABASE_URL.get_secret_value(),
        application_name="relaypay-commerce",
    )
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        engine.dispose()

    app = FastAPI(title="RelayPay Synthetic Commerce", version="0.8.0", lifespan=lifespan)

    @app.exception_handler(RelayPayError)
    async def handle_error(_, error: RelayPayError):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=error.http_status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            },
        )

    def authenticate(value: str | None) -> None:
        expected = resolved.COMMERCE_CONTROL_SECRET.get_secret_value()
        if value is None or not hmac.compare_digest(value, expected):
            raise RelayPayError("UNAUTHENTICATED", "Authentication required", 401)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/connector")
    def connector_health() -> dict[str, str]:
        return {"status": "healthy", "capability": "commerce.orders"}

    @app.post("/v1/orders", status_code=201)
    def post_order(
        payload: OrderCreate,
        control: Annotated[str | None, Header(alias="X-Commerce-Control")] = None,
    ) -> dict[str, object]:
        authenticate(control)
        order = create_order(
            factory,
            account_public_id=payload.account_id,
            external_reference=payload.external_reference,
            total_amount=payload.total_amount,
        )
        return {
            "id": order.public_id,
            "externalReference": order.external_reference,
            "totalAmount": order.total_amount,
            "currency": order.currency,
            "status": order.status,
        }

    @app.post("/v1/orders/{order_id}/payment-links", status_code=204)
    def post_payment_link(
        order_id: str,
        payload: PaymentLinkCreate,
        control: Annotated[str | None, Header(alias="X-Commerce-Control")] = None,
    ) -> None:
        authenticate(control)
        link_payment(
            factory,
            order_public_id=order_id,
            relaypay_payment_id=payload.relaypay_payment_id,
            amount=payload.amount,
        )

    @app.post("/v1/events/{event_id}", status_code=204)
    def post_event(
        event_id: str,
        payload: dict[str, object],
        control: Annotated[str | None, Header(alias="X-Commerce-Control")] = None,
    ) -> None:
        authenticate(control)
        body = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        synchronize_event(factory, body, event_id)

    @app.get("/control/credential-digest")
    def credential_digest(
        control: Annotated[str | None, Header(alias="X-Commerce-Control")] = None,
    ) -> dict[str, str]:
        authenticate(control)
        return {"sha256": hashlib.sha256(control.encode()).hexdigest() if control else ""}

    install_asgi_metrics(app, resolved, service="commerce")
    instrument_fastapi(app, resolved, service_name="relaypay-commerce", engine=engine)
    return app
