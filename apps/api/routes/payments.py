import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from relaypay.config import Settings
from relaypay.contracts import CustomerCreate, EmptyCommand, PaymentIntentCreate, RefundCreate
from relaypay.errors import RelayPayError
from relaypay.idempotency import build_fingerprint, canonical_json_bytes
from relaypay.identity.security import Principal, authenticate_api_key, require_scopes
from relaypay.payments.service import (
    HTTPResult,
    create_customer,
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
    initiate_refund,
    read_operation,
    read_payment,
)
from relaypay.provider_operations.service import ProviderTransport, dispatch_operation
from sqlalchemy.orm import Session, sessionmaker


def _http_response(result: HTTPResult) -> Response:
    return Response(content=result.body, status_code=result.status_code, headers=result.headers)


def build_payments_router(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    provider_transport: ProviderTransport,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["payments"])

    def merchant_principal(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> Principal:
        if authorization is None or not authorization.startswith("Bearer "):
            raise RelayPayError(
                code="UNAUTHENTICATED", message="Authentication required", http_status=401
            )
        plaintext = authorization.removeprefix("Bearer ").strip()
        with session_factory() as session, session.begin():
            return authenticate_api_key(
                session,
                plaintext=plaintext,
                pepper=settings.API_KEY_PEPPER.get_secret_value(),
            )

    def required_idempotency_key(
        value: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> str:
        if value is None or not value.strip():
            raise RelayPayError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="Idempotency-Key header is required for this command",
                http_status=400,
            )
        normalized = value.strip()
        if len(normalized) > 255:
            raise RelayPayError(
                code="IDEMPOTENCY_KEY_INVALID",
                message="Idempotency-Key must not exceed 255 characters",
                http_status=400,
            )
        return normalized

    PrincipalDep = Annotated[Principal, Depends(merchant_principal)]
    IdempotencyKeyDep = Annotated[str, Depends(required_idempotency_key)]

    def dispatch_after_commit(result: HTTPResult, principal: Principal) -> HTTPResult:
        if result.status_code == 202:
            operation_id = str(json.loads(result.body)["operationId"])
            dispatch_operation(
                session_factory,
                organisation_id=principal.organisation_id,
                environment_id=principal.environment_id,
                operation_public_id=operation_id,
                provider_account_id=settings.PROVIDER_ACCOUNT_ID,
                provider_signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
                transport=provider_transport,
            )
            return read_operation(
                session_factory,
                organisation_id=principal.organisation_id,
                environment_id=principal.environment_id,
                operation_public_id=operation_id,
            )
        return result

    @router.post("/customers", status_code=201)
    def post_customer(payload: CustomerCreate, principal: PrincipalDep) -> Response:
        require_scopes(principal, "customers:write")
        customer = create_customer(
            session_factory,
            organisation_id=principal.organisation_id,
            environment_id=principal.environment_id,
            payload=payload,
        )
        return Response(
            content=canonical_json_bytes(
                {
                    "id": customer.public_id,
                    "merchantCustomerReference": customer.merchant_customer_reference,
                    "displayName": customer.display_name,
                }
            ),
            status_code=201,
            media_type="application/json",
        )

    @router.post("/payment_intents", status_code=201)
    def post_payment_intent(
        payload: PaymentIntentCreate,
        principal: PrincipalDep,
        idempotency_key: IdempotencyKeyDep,
    ) -> Response:
        require_scopes(principal, "payments:write")
        fingerprint = build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents",
            path_params={},
            body=payload,
        )
        return _http_response(
            create_payment_intent(
                session_factory,
                organisation_id=principal.organisation_id,
                environment_id=principal.environment_id,
                payload=payload,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
            )
        )

    @router.get("/payment_intents/{payment_intent_id}")
    def get_payment_intent(payment_intent_id: str, principal: PrincipalDep) -> Response:
        require_scopes(principal, "payments:read")
        return _http_response(
            read_payment(
                session_factory,
                organisation_id=principal.organisation_id,
                environment_id=principal.environment_id,
                payment_public_id=payment_intent_id,
            )
        )

    @router.post("/payment_intents/{payment_intent_id}/authorize", status_code=202)
    def post_authorize(
        payment_intent_id: str,
        payload: EmptyCommand,
        principal: PrincipalDep,
        idempotency_key: IdempotencyKeyDep,
    ) -> Response:
        require_scopes(principal, "payments:write")
        return _http_response(
            dispatch_after_commit(
                initiate_authorization(
                    session_factory,
                    organisation_id=principal.organisation_id,
                    environment_id=principal.environment_id,
                    payment_public_id=payment_intent_id,
                    idempotency_key=idempotency_key,
                    fingerprint=build_fingerprint(
                        api_version="v1",
                        method="POST",
                        route_template="/payment_intents/{payment_intent_id}/authorize",
                        path_params={"payment_intent_id": payment_intent_id},
                        body=payload,
                    ),
                    key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
                ),
                principal,
            )
        )

    @router.post("/payment_intents/{payment_intent_id}/capture", status_code=202)
    def post_capture(
        payment_intent_id: str,
        payload: EmptyCommand,
        principal: PrincipalDep,
        idempotency_key: IdempotencyKeyDep,
    ) -> Response:
        require_scopes(principal, "payments:write")
        return _http_response(
            dispatch_after_commit(
                initiate_capture(
                    session_factory,
                    organisation_id=principal.organisation_id,
                    environment_id=principal.environment_id,
                    payment_public_id=payment_intent_id,
                    idempotency_key=idempotency_key,
                    fingerprint=build_fingerprint(
                        api_version="v1",
                        method="POST",
                        route_template="/payment_intents/{payment_intent_id}/capture",
                        path_params={"payment_intent_id": payment_intent_id},
                        body=payload,
                    ),
                    key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
                ),
                principal,
            )
        )

    @router.post("/payment_intents/{payment_intent_id}/refunds", status_code=202)
    def post_refund(
        payment_intent_id: str,
        payload: RefundCreate,
        principal: PrincipalDep,
        idempotency_key: IdempotencyKeyDep,
    ) -> Response:
        require_scopes(principal, "payments:write")
        return _http_response(
            dispatch_after_commit(
                initiate_refund(
                    session_factory,
                    organisation_id=principal.organisation_id,
                    environment_id=principal.environment_id,
                    payment_public_id=payment_intent_id,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    fingerprint=build_fingerprint(
                        api_version="v1",
                        method="POST",
                        route_template="/payment_intents/{payment_intent_id}/refunds",
                        path_params={"payment_intent_id": payment_intent_id},
                        body=payload,
                    ),
                    key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
                ),
                principal,
            )
        )

    @router.get("/operations/{operation_id}")
    def get_operation(operation_id: str, principal: PrincipalDep) -> Response:
        require_scopes(principal, "payments:read")
        return _http_response(
            read_operation(
                session_factory,
                organisation_id=principal.organisation_id,
                environment_id=principal.environment_id,
                operation_public_id=operation_id,
            )
        )

    return router
