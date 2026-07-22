import hmac
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from relaypay.config import Settings, get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.mock_provider.models import ProviderAccount, ProviderEffect
from relaypay.mock_provider.service import (
    EffectCommand,
    apply_effect,
    configure_fault,
    export_statement,
    lookup_effect,
)
from sqlalchemy import func, select


class EffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: str = Field(alias="accountId", min_length=1, max_length=64)
    stable_key: str = Field(alias="stableKey", min_length=1, max_length=128)
    operation_kind: Literal["AUTHORIZE", "CAPTURE", "REFUND"] = Field(alias="operationKind")
    reference: str = Field(min_length=1, max_length=128)
    amount: int = Field(strict=True, gt=0)
    currency: Literal["INR"]


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: str = Field(alias="accountId", min_length=1, max_length=64)
    stable_key: str = Field(alias="stableKey", min_length=1, max_length=128)
    fault_type: Literal[
        "LOSE_RESPONSE", "DECLINE", "MALFORMED", "UNSIGNED", "MISMATCHED", "PENDING"
    ] = Field(alias="faultType")


class StatementExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(alias="accountId", min_length=1, max_length=64)
    source_reference: str = Field(alias="sourceReference", min_length=1, max_length=128)
    period_start: AwareDatetime = Field(alias="periodStart")
    period_end: AwareDatetime = Field(alias="periodEnd")


def _error_response(error: RelayPayError) -> JSONResponse:
    return JSONResponse(
        status_code=error.http_status,
        content={"error": {"code": error.code, "message": error.message}},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    engine = build_engine(
        resolved.PROVIDER_DATABASE_URL.get_secret_value(), application_name="relaypay-provider"
    )
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        engine.dispose()

    app = FastAPI(title="RelayPay Mock Provider", version="0.3.0", lifespan=lifespan)

    @app.exception_handler(RelayPayError)
    async def handle_relaypay_error(_, error: RelayPayError):  # type: ignore[no-untyped-def]
        return _error_response(error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_, error: RequestValidationError):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": [
                        {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
                        for item in error.errors()
                    ],
                }
            },
        )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.post("/v1/effects")
    def mutate(payload: EffectRequest) -> Response:
        result = apply_effect(
            factory,
            command=EffectCommand(
                account_id=payload.account_id,
                stable_key=payload.stable_key,
                operation_kind=payload.operation_kind,
                reference=payload.reference,
                amount=payload.amount,
                currency=payload.currency,
            ),
            signing_secret=resolved.PROVIDER_SIGNING_SECRET.get_secret_value(),
        )
        return Response(content=result.body, status_code=result.status_code, headers=result.headers)

    @app.get("/v1/effects/{stable_key}")
    def lookup(stable_key: str, account_id: str) -> Response:
        result = lookup_effect(
            factory,
            account_public_id=account_id,
            stable_key=stable_key,
            signing_secret=resolved.PROVIDER_SIGNING_SECRET.get_secret_value(),
        )
        return Response(content=result.body, status_code=result.status_code, headers=result.headers)

    @app.post("/control/faults", status_code=204)
    def fault(
        payload: FaultRequest,
        control_secret: Annotated[str | None, Header(alias="X-Provider-Control")] = None,
    ) -> Response:
        expected = resolved.PROVIDER_CONTROL_SECRET.get_secret_value()
        if control_secret is None or not hmac.compare_digest(control_secret, expected):
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

    @app.get("/control/effects/{stable_key}/proof")
    def effect_proof(
        stable_key: str,
        account_id: str,
        control_secret: Annotated[str | None, Header(alias="X-Provider-Control")] = None,
    ) -> dict[str, int | str]:
        expected = resolved.PROVIDER_CONTROL_SECRET.get_secret_value()
        if control_secret is None or not hmac.compare_digest(control_secret, expected):
            raise RelayPayError(
                code="UNAUTHENTICATED", message="Authentication required", http_status=401
            )
        with factory() as session, session.begin():
            count = session.scalar(
                select(func.count())
                .select_from(ProviderEffect)
                .join(ProviderAccount, ProviderAccount.id == ProviderEffect.provider_account_id)
                .where(
                    ProviderAccount.public_id == account_id,
                    ProviderEffect.stable_key == stable_key,
                )
            )
        return {"stableKey": stable_key, "effectCount": count or 0}

    @app.post("/control/statements")
    def statement_export(
        payload: StatementExportRequest,
        control_secret: Annotated[str | None, Header(alias="X-Provider-Control")] = None,
    ) -> Response:
        expected = resolved.PROVIDER_CONTROL_SECRET.get_secret_value()
        if control_secret is None or not hmac.compare_digest(control_secret, expected):
            raise RelayPayError(
                code="UNAUTHENTICATED", message="Authentication required", http_status=401
            )
        result = export_statement(
            factory,
            account_public_id=payload.account_id,
            source_reference=payload.source_reference,
            period_start=payload.period_start,
            period_end=payload.period_end,
        )
        return Response(
            content=result.body,
            media_type="application/json",
            headers={
                "X-Statement-Id": result.public_id,
                "X-Statement-SHA256": result.sha256,
                "X-Statement-Item-Count": str(result.item_count),
            },
        )

    return app
