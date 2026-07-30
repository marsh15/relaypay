import hashlib
import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from relaypay.config import Settings, get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.mock_bank.service import (
    BankTransferCommand,
    apply_transfer,
    configure_fault,
    lookup_transfer,
)


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_id: str = Field(alias="accountId", min_length=1, max_length=64)
    stable_key: str = Field(alias="stableKey", min_length=1, max_length=128)
    beneficiary_reference: str = Field(alias="beneficiaryReference", min_length=1, max_length=128)
    payout_reference: str = Field(alias="payoutReference", min_length=1, max_length=128)
    amount: int = Field(strict=True, gt=0)
    currency: Literal["INR"]


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_id: str = Field(alias="accountId", min_length=1, max_length=64)
    stable_key: str = Field(alias="stableKey", min_length=1, max_length=128)
    fault_type: Literal["LOSE_RESPONSE", "DECLINE", "PENDING"] = Field(alias="faultType")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    engine = build_engine(
        resolved.BANK_DATABASE_URL.get_secret_value(), application_name="relaypay-bank"
    )
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        engine.dispose()

    app = FastAPI(title="RelayPay Synthetic Bank", version="0.6.0", lifespan=lifespan)

    @app.exception_handler(RelayPayError)
    async def handle_error(_, error: RelayPayError):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=error.http_status,
            content={"error": {"code": error.code, "message": error.message}},
        )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.post("/v1/transfers")
    def transfer(payload: TransferRequest) -> Response:
        result = apply_transfer(
            factory,
            command=BankTransferCommand(
                account_id=payload.account_id,
                stable_key=payload.stable_key,
                beneficiary_reference=payload.beneficiary_reference,
                payout_reference=payload.payout_reference,
                amount=payload.amount,
                currency=payload.currency,
            ),
            signing_secret=resolved.BANK_SIGNING_SECRET.get_secret_value(),
        )
        return Response(content=result.body, status_code=result.status_code, headers=result.headers)

    @app.get("/v1/transfers/{stable_key}")
    def lookup(stable_key: str, account_id: str) -> Response:
        result = lookup_transfer(
            factory,
            account_public_id=account_id,
            stable_key=stable_key,
            signing_secret=resolved.BANK_SIGNING_SECRET.get_secret_value(),
        )
        return Response(content=result.body, status_code=result.status_code, headers=result.headers)

    @app.post("/control/faults", status_code=204)
    def fault(
        payload: FaultRequest,
        control_secret: Annotated[str | None, Header(alias="X-Bank-Control")] = None,
    ) -> Response:
        if control_secret is None or not hmac.compare_digest(
            control_secret, resolved.BANK_CONTROL_SECRET.get_secret_value()
        ):
            raise RelayPayError(
                code="UNAUTHENTICATED", message="Authentication required", http_status=401
            )
        configure_fault(
            factory,
            account_public_id=payload.account_id,
            stable_key=payload.stable_key,
            fault_type=payload.fault_type,
        )
        return Response(status_code=204)

    @app.get("/control/signing-digest")
    def signing_digest(
        control_secret: Annotated[str | None, Header(alias="X-Bank-Control")] = None,
    ) -> dict[str, str]:
        if control_secret is None or not hmac.compare_digest(
            control_secret, resolved.BANK_CONTROL_SECRET.get_secret_value()
        ):
            raise RelayPayError(
                code="UNAUTHENTICATED", message="Authentication required", http_status=401
            )
        return {
            "sha256": hashlib.sha256(
                resolved.BANK_SIGNING_SECRET.get_secret_value().encode()
            ).hexdigest()
        }

    return app
