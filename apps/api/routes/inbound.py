from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from relaypay.config import Settings
from relaypay.connectors.service import accept_inbound_webhook
from sqlalchemy.orm import Session, sessionmaker


def build_inbound_router(
    *, settings: Settings, session_factory: sessionmaker[Session]
) -> APIRouter:
    router = APIRouter(prefix="/api/inbound/v1", tags=["inbound-webhooks"])

    @router.post("/connectors/{connector_id}/events", status_code=202)
    async def post_event(
        connector_id: str,
        request: Request,
        event_id: Annotated[str, Header(alias="X-Provider-Event-ID")],
        timestamp: Annotated[str, Header(alias="X-Provider-Timestamp")],
        signature: Annotated[str, Header(alias="X-Provider-Signature")],
    ) -> Response:
        body = await request.body()
        _, replayed = accept_inbound_webhook(
            session_factory,
            connector_public_id=connector_id,
            provider_event_id=event_id,
            timestamp_text=timestamp,
            signature=signature,
            body=body,
            secret=settings.COMMERCE_CONTROL_SECRET.get_secret_value(),
            replay_seconds=settings.INBOUND_WEBHOOK_REPLAY_SECONDS,
        )
        return Response(
            status_code=200 if replayed else 202,
            headers={"Inbound-Replayed": "true"} if replayed else None,
        )

    return router
