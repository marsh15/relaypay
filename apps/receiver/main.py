from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from relaypay.config import Settings, get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.receiver.service import (
    ReceiverContradictionError,
    ReceiverValidationError,
    receive_event,
)
from sqlalchemy import text


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    engine = build_engine(
        resolved.RECEIVER_DATABASE_URL.get_secret_value(), application_name="relaypay-receiver"
    )
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        engine.dispose()

    app = FastAPI(title="RelayPay Receiver", version="0.2.0", lifespan=lifespan)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        with factory() as session, session.begin():
            session.execute(text("SELECT 1 FROM receiver.received_events LIMIT 1"))
        return {"status": "ready", "database": "available"}

    @app.post("/webhooks/relaypay")
    async def webhook(
        request: Request,
        event_id: Annotated[str, Header(alias="X-RelayPay-Event-Id")],
        timestamp: Annotated[str, Header(alias="X-RelayPay-Timestamp")],
        signature: Annotated[str, Header(alias="X-RelayPay-Signature")],
    ) -> JSONResponse:
        body = await request.body()
        try:
            with factory() as session, session.begin():
                result = receive_event(
                    session,
                    body=body,
                    event_id=event_id,
                    timestamp_text=timestamp,
                    signature=signature,
                    secret=resolved.RECEIVER_WEBHOOK_SECRET.get_secret_value(),
                )
        except ReceiverValidationError:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "INVALID_WEBHOOK", "message": "Invalid webhook"}},
            )
        except ReceiverContradictionError:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "EVENT_CONTRADICTION",
                        "message": "Event bytes contradict a stored event",
                    }
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "received": True,
                "eventId": result.event_id,
                "duplicate": result.duplicate,
                "deliveryCount": result.delivery_count,
            },
        )

    return app
