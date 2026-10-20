"""/api/v2: payment commands whose behaviour v2.0 changed or added.

v2 adds an authorization hold, partial capture, void, and a payment view with the persisted status
and the capture's fee split. Creating payments, refunds, listing and operation reads are
unchanged and stay on /api/v1, whose bytes are frozen (scripts/check_api_compatibility.py).
Fingerprints use ``api_version="v2"``, so a key used on a v1 route can never replay a v2 command.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from relaypay.config import Settings
from relaypay.contracts import AuthorizeCommandV2, CaptureCommandV2, EmptyCommand
from relaypay.idempotency import Fingerprint, build_fingerprint
from relaypay.identity.security import Principal, require_scopes
from relaypay.payments.service import (
    initiate_authorization,
    initiate_capture,
    initiate_void,
    read_payment_v2,
)
from relaypay.provider_operations.service import ProviderTransport
from sqlalchemy.orm import Session, sessionmaker

from apps.api.routes.payments import _http_response, merchant_dependencies


def build_payments_v2_router(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    provider_transport: ProviderTransport,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["payments-v2"])
    deps = merchant_dependencies(
        settings=settings,
        session_factory=session_factory,
        provider_transport=provider_transport,
    )
    PrincipalDep = Annotated[Principal, Depends(deps.principal)]
    IdempotencyKeyDep = Annotated[str, Depends(deps.idempotency_key)]
    key_pepper = settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value()

    def fingerprint(payment_intent_id: str, command: str, body: BaseModel) -> Fingerprint:
        return build_fingerprint(
            api_version="v2",
            method="POST",
            route_template=f"/payment_intents/{{payment_intent_id}}/{command}",
            path_params={"payment_intent_id": payment_intent_id},
            body=body,
        )

    @router.get("/payment_intents/{payment_intent_id}")
    def get_payment_intent(payment_intent_id: str, principal: PrincipalDep) -> Response:
        require_scopes(principal, "payments:read")
        return _http_response(
            read_payment_v2(
                session_factory,
                organisation_id=principal.organisation_id,
                environment_id=principal.environment_id,
                payment_public_id=payment_intent_id,
            )
        )

    @router.post("/payment_intents/{payment_intent_id}/authorize", status_code=202)
    def post_authorize(
        payment_intent_id: str,
        payload: AuthorizeCommandV2,
        principal: PrincipalDep,
        idempotency_key: IdempotencyKeyDep,
    ) -> Response:
        require_scopes(principal, "payments:write")
        return _http_response(
            deps.dispatch_after_commit(
                initiate_authorization(
                    session_factory,
                    organisation_id=principal.organisation_id,
                    environment_id=principal.environment_id,
                    payment_public_id=payment_intent_id,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint(payment_intent_id, "authorize", payload),
                    key_pepper=key_pepper,
                    hold_seconds=payload.hold_seconds or settings.AUTH_HOLD_SECONDS,
                ),
                principal,
            )
        )

    @router.post("/payment_intents/{payment_intent_id}/capture", status_code=202)
    def post_capture(
        payment_intent_id: str,
        payload: CaptureCommandV2,
        principal: PrincipalDep,
        idempotency_key: IdempotencyKeyDep,
    ) -> Response:
        require_scopes(principal, "payments:write")
        return _http_response(
            deps.dispatch_after_commit(
                initiate_capture(
                    session_factory,
                    organisation_id=principal.organisation_id,
                    environment_id=principal.environment_id,
                    payment_public_id=payment_intent_id,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint(payment_intent_id, "capture", payload),
                    key_pepper=key_pepper,
                    amount=payload.amount,
                ),
                principal,
            )
        )

    @router.post("/payment_intents/{payment_intent_id}/void", status_code=202)
    def post_void(
        payment_intent_id: str,
        payload: EmptyCommand,
        principal: PrincipalDep,
        idempotency_key: IdempotencyKeyDep,
    ) -> Response:
        require_scopes(principal, "payments:write")
        return _http_response(
            deps.dispatch_after_commit(
                initiate_void(
                    session_factory,
                    organisation_id=principal.organisation_id,
                    environment_id=principal.environment_id,
                    payment_public_id=payment_intent_id,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint(payment_intent_id, "void", payload),
                    key_pepper=key_pepper,
                ),
                principal,
            )
        )

    return router
