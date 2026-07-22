import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from relaypay.config import Settings, get_settings
from relaypay.contracts import EmptyCommand
from relaypay.database import build_engine, build_session_factory
from relaypay.demo_scenarios.service import HTTPScenarioFaultController, ScenarioFaultController
from relaypay.errors import RelayPayError
from relaypay.event_delivery.delivery import HTTPWebhookTransport, WebhookTransport
from relaypay.evidence.service import payment_evidence
from relaypay.identity.rate_limit import FixedWindowRateLimiter
from relaypay.identity.security import (
    Principal,
    authenticate_session,
    issue_session,
    revoke_session,
    rotate_csrf,
    verify_csrf,
)
from relaypay.identity.service import require_organisation_admin
from relaypay.payments.service import read_operation
from relaypay.provider_operations.recovery import claim_specific_operation, recover_claim
from relaypay.provider_operations.service import HTTPProviderTransport, ProviderTransport
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from apps.api.routes.admin import build_admin_router
from apps.api.routes.payments import build_payments_router

logger = logging.getLogger("relaypay.api")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: Annotated[
        str,
        Field(
            min_length=3,
            max_length=320,
            pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
        ),
    ]
    password: Annotated[str, Field(min_length=8, max_length=256)]
    organisation_id: Annotated[
        str | None, Field(alias="organisationId", pattern=r"^org_[0-9a-f]{32}$")
    ] = None


class SessionResponse(BaseModel):
    userId: str
    displayName: str
    organisationId: str
    organisationRole: str | None
    platformRole: str
    csrfToken: str
    expiresAt: str | None = None


def _error_response(error: RelayPayError) -> JSONResponse:
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else None
    return JSONResponse(
        status_code=error.http_status,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details or None,
            }
        },
        headers=headers,
    )


def create_app(
    settings: Settings | None = None,
    provider_transport: ProviderTransport | None = None,
    scenario_fault_controller: ScenarioFaultController | None = None,
    webhook_transport: WebhookTransport | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    engine = build_engine(
        resolved.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="relaypay-api"
    )
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        yield
        engine.dispose()

    app = FastAPI(title="RelayPay API", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.login_limiter = FixedWindowRateLimiter(limit=5, window_seconds=60)
    transport = provider_transport or HTTPProviderTransport(base_url=resolved.PROVIDER_BASE_URL)
    scenario_transport = provider_transport or HTTPProviderTransport(
        base_url=resolved.PROVIDER_BASE_URL,
        timeout_seconds=10.0,
    )
    app.include_router(
        build_payments_router(
            settings=resolved,
            session_factory=session_factory,
            provider_transport=transport,
        )
    )

    @app.exception_handler(RelayPayError)
    async def handle_relaypay_error(_: Request, error: RelayPayError) -> JSONResponse:
        return _error_response(error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": {
                        "errors": [
                            {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
                            for item in error.errors()
                        ]
                    },
                }
            },
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", f"req_{uuid.uuid4().hex}")
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["Server-Timing"] = f"app;dur={(time.monotonic() - started) * 1000:.1f}"
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "requestId": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "durationMs": round((time.monotonic() - started) * 1000, 1),
                },
                separators=(",", ":"),
            )
        )
        return response

    def get_session_factory(request: Request) -> sessionmaker[Session]:
        return request.app.state.session_factory  # type: ignore[no-any-return]

    def get_principal(
        request: Request,
        token: Annotated[str | None, Cookie(alias=resolved.SESSION_COOKIE_NAME)] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> Principal:
        if token is None:
            if authorization is not None and authorization.startswith("Bearer "):
                raise RelayPayError(
                    code="ADMIN_SESSION_REQUIRED",
                    message="This operation requires an administrator session",
                    http_status=403,
                )
            raise RelayPayError(
                code="UNAUTHENTICATED", message="Authentication required", http_status=401
            )
        factory: sessionmaker[Session] = request.app.state.session_factory
        with factory() as session, session.begin():
            return authenticate_session(
                session,
                token=token,
                session_secret=resolved.SESSION_SECRET.get_secret_value(),
            )

    receiver_url = f"{resolved.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay"
    app.include_router(
        build_admin_router(
            settings=resolved,
            session_factory=session_factory,
            provider_transport=scenario_transport,
            fault_controller=scenario_fault_controller
            or HTTPScenarioFaultController(resolved, timeout_seconds=10.0),
            webhook_transport=webhook_transport
            or HTTPWebhookTransport(allowed_url=receiver_url, timeout_seconds=30.0),
            principal_dependency=get_principal,
        )
    )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready(
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> dict[str, str]:
        try:
            with factory() as session, session.begin():
                session.execute(text("SELECT 1 FROM webhook_deliveries LIMIT 1"))
        except Exception as exc:
            raise RelayPayError(
                code="DEPENDENCY_UNAVAILABLE",
                message="A required dependency is unavailable",
                http_status=503,
                retry_after=5,
            ) from exc
        return {"status": "ready", "database": "available"}

    @app.get("/api/v1/payment_intents/{payment_id}/evidence")
    def get_payment_evidence(
        payment_id: str,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            evidence = payment_evidence(
                session,
                organisation_id=principal.organisation_id,
                payment_public_id=payment_id,
            )
        if evidence is None:
            raise RelayPayError(
                code="NOT_FOUND", message="Payment intent not found", http_status=404
            )
        return evidence

    @app.post("/api/session/login", response_model=SessionResponse)
    def login(payload: LoginRequest, request: Request, response: Response) -> SessionResponse:
        client_host = request.client.host if request.client else "unknown"
        request.app.state.login_limiter.check(client_host)
        with session_factory() as session, session.begin():
            issued = issue_session(
                session,
                email=str(payload.email),
                password=payload.password,
                session_secret=resolved.SESSION_SECRET.get_secret_value(),
                csrf_secret=resolved.CSRF_SECRET.get_secret_value(),
                organisation_public_id=payload.organisation_id,
            )
        response.set_cookie(
            key=resolved.SESSION_COOKIE_NAME,
            value=issued.token,
            max_age=8 * 60 * 60,
            expires=issued.expires_at,
            path="/",
            secure=resolved.APP_ENV == "production",
            httponly=True,
            samesite="lax",
        )
        return SessionResponse(
            userId=str(issued.principal.user_id),
            displayName=issued.principal.display_name,
            organisationId=issued.principal.organisation_public_id,
            organisationRole=issued.principal.membership_role,
            platformRole=issued.principal.platform_role,
            csrfToken=issued.csrf_token,
            expiresAt=issued.expires_at.isoformat(),
        )

    @app.get("/api/session/me", response_model=SessionResponse)
    def me(principal: Annotated[Principal, Depends(get_principal)]) -> SessionResponse:
        with session_factory() as session, session.begin():
            csrf_token = rotate_csrf(session, principal, resolved.CSRF_SECRET.get_secret_value())
        return SessionResponse(
            userId=str(principal.user_id),
            displayName=principal.display_name,
            organisationId=principal.organisation_public_id,
            organisationRole=principal.membership_role,
            platformRole=principal.platform_role,
            csrfToken=csrf_token,
        )

    @app.post("/api/session/logout")
    def logout(
        principal: Annotated[Principal, Depends(get_principal)],
        response: Response,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, bool]:
        with session_factory() as session, session.begin():
            verify_csrf(
                session,
                principal=principal,
                csrf_token=csrf_token,
                csrf_secret=resolved.CSRF_SECRET.get_secret_value(),
            )
            revoke_session(session, principal)
        response.delete_cookie(
            resolved.SESSION_COOKIE_NAME,
            path="/",
            secure=resolved.APP_ENV == "production",
            httponly=True,
            samesite="lax",
        )
        return {"loggedOut": True}

    @app.post("/api/v1/operations/{operation_id}/retry_lookup")
    def retry_provider_lookup(
        operation_id: str,
        _payload: EmptyCommand,
        principal: Annotated[Principal, Depends(get_principal)],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Response:
        with session_factory() as session, session.begin():
            verify_csrf(
                session,
                principal=principal,
                csrf_token=csrf_token,
                csrf_secret=resolved.CSRF_SECRET.get_secret_value(),
            )
        require_organisation_admin(principal)
        claim = claim_specific_operation(
            session_factory,
            organisation_id=principal.organisation_id,
            operation_public_id=operation_id,
            require_review=True,
        )
        recover_claim(
            session_factory,
            claim=claim,
            provider_account_id=resolved.PROVIDER_ACCOUNT_ID,
            provider_signing_secret=resolved.PROVIDER_SIGNING_SECRET.get_secret_value(),
            transport=transport,
            actor_type="ADMIN_LOOKUP",
        )
        result = read_operation(
            session_factory,
            organisation_id=principal.organisation_id,
            operation_public_id=operation_id,
        )
        return Response(content=result.body, status_code=result.status_code, headers=result.headers)

    return app
