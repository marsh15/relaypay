"""Inbound PSP webhook endpoint: verify, match and store in the raw inbox (M17)."""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from relaypay.config import Settings
from relaypay.idempotency import canonical_json_bytes
from relaypay.psp.inbox import accept_psp_event
from sqlalchemy.orm import Session, sessionmaker


def build_inbound_psp_router(
    *, settings: Settings, session_factory: sessionmaker[Session]
) -> APIRouter:
    router = APIRouter(prefix="/api/inbound/v1/psp", tags=["inbound-psp-webhooks"])

    @router.post("/{provider}")
    async def post_psp_event(provider: str, request: Request) -> Response:
        body = await request.body()
        event, replayed = accept_psp_event(
            session_factory,
            provider=provider.upper(),
            headers=dict(request.headers),
            body=body,
            webhook_secret=settings.PROVIDER_WEBHOOK_SECRET.get_secret_value(),
            replay_seconds=settings.PSP_WEBHOOK_REPLAY_SECONDS,
        )
        return Response(
            content=canonical_json_bytes(
                {"id": event.public_id, "replayed": replayed, "status": event.status}
            ),
            status_code=200,
            media_type="application/json",
        )

    return router
