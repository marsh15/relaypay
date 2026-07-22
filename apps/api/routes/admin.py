from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from relaypay.config import Settings
from relaypay.demo_scenarios.service import (
    ScenarioFaultController,
    read_scenario_run,
    run_lost_capture_scenario,
)
from relaypay.event_delivery.admin import read_delivery, replay_delivery
from relaypay.event_delivery.delivery import WebhookTransport
from relaypay.identity.security import Principal, verify_csrf
from relaypay.identity.service import (
    activate_api_key_version,
    create_api_key,
    list_environments,
    list_memberships,
    provision_organisation,
    require_organisation_admin,
    revoke_api_key,
    rotate_api_key,
    set_api_key_scopes,
    set_membership,
)
from relaypay.provider_operations.service import ProviderTransport
from relaypay.reconciliation.service import MAX_STATEMENT_BYTES, import_statement
from sqlalchemy.orm import Session, sessionmaker


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scenario_type: Literal["LOST_CAPTURE_RESPONSE"] = Field(alias="scenarioType")


class APIKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=32)


class OrganisationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)


class MembershipUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str = Field(min_length=3, max_length=320)
    role: Literal["ORGANISATION_ADMIN", "DEVELOPER", "VIEWER"]
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"


class APIKeyScopesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scopes: list[str] = Field(min_length=1, max_length=32)


def build_admin_router(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    provider_transport: ProviderTransport,
    fault_controller: ScenarioFaultController,
    webhook_transport: WebhookTransport,
    principal_dependency: Callable[..., Principal],
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["admin"])
    PrincipalDep = Annotated[Principal, Depends(principal_dependency)]

    def require_csrf(principal: Principal, csrf_token: str | None) -> None:
        with session_factory() as session, session.begin():
            verify_csrf(
                session,
                principal=principal,
                csrf_token=csrf_token,
                csrf_secret=settings.CSRF_SECRET.get_secret_value(),
            )

    @router.get("/admin/v1/environments")
    def get_environments(principal: PrincipalDep) -> list[dict[str, str]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": item.public_id,
                    "name": item.name,
                    "type": item.environment_type,
                    "status": item.status,
                }
                for item in list_environments(session, principal)
            ]

    @router.post("/admin/v1/organisations", status_code=201)
    def post_organisation(
        payload: OrganisationCreate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, str]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation = provision_organisation(session, principal=principal, name=payload.name)
            return {"id": organisation.public_id, "name": organisation.name}

    @router.get("/admin/v1/memberships")
    def get_memberships(principal: PrincipalDep) -> list[dict[str, str]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "userId": str(user.id),
                    "email": user.email_normalized,
                    "displayName": user.display_name,
                    "role": membership.role,
                    "status": membership.status,
                }
                for membership, user in list_memberships(session, principal)
            ]

    @router.put("/admin/v1/memberships")
    def put_membership(
        payload: MembershipUpdate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, str]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            membership = set_membership(
                session,
                principal=principal,
                email=payload.email,
                role=payload.role,
                status=payload.status,
            )
            return {
                "userId": str(membership.user_id),
                "role": membership.role,
                "status": membership.status,
            }

    @router.post("/admin/v1/environments/{environment_id}/api-keys", status_code=201)
    def post_api_key(
        environment_id: str,
        payload: APIKeyCreate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            key, version, issued = create_api_key(
                session,
                principal=principal,
                environment_public_id=environment_id,
                name=payload.name,
                scopes=payload.scopes,
                pepper=settings.API_KEY_PEPPER.get_secret_value(),
            )
            return {
                "id": key.public_id,
                "version": version.version,
                "secret": issued.plaintext,
                "status": version.status,
            }

    @router.post("/admin/v1/environments/{environment_id}/statement-imports")
    async def post_statement_import(
        environment_id: str,
        principal: PrincipalDep,
        provider: Annotated[Literal["PAYMENT_PROVIDER"], Form()],
        source_reference: Annotated[
            str, Form(alias="sourceReference", min_length=1, max_length=128)
        ],
        source_format: Annotated[Literal["CSV", "JSON"], Form(alias="sourceFormat")],
        period_start: Annotated[AwareDatetime, Form(alias="periodStart")],
        period_end: Annotated[AwareDatetime, Form(alias="periodEnd")],
        statement: Annotated[UploadFile, File()],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> JSONResponse:
        require_csrf(principal, csrf_token)
        raw_bytes = await statement.read(MAX_STATEMENT_BYTES + 1)
        with session_factory() as session, session.begin():
            result = import_statement(
                session,
                principal=principal,
                environment_public_id=environment_id,
                provider=provider,
                source_reference=source_reference,
                source_format=source_format,
                period_start=period_start,
                period_end=period_end,
                raw_bytes=raw_bytes,
            )
            body = {
                "id": result.statement_import.public_id,
                "runId": result.reconciliation_run.public_id,
                "runStatus": result.reconciliation_run.status,
                "sha256": result.statement_import.raw_sha256.hex(),
            }
        return JSONResponse(status_code=201 if result.created else 200, content=body)

    @router.post(
        "/admin/v1/environments/{environment_id}/api-keys/{key_id}/rotate",
        status_code=201,
    )
    def post_api_key_rotation(
        environment_id: str,
        key_id: str,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            version, issued = rotate_api_key(
                session,
                principal=principal,
                environment_public_id=environment_id,
                key_public_id=key_id,
                pepper=settings.API_KEY_PEPPER.get_secret_value(),
            )
            return {
                "version": version.version,
                "secret": issued.plaintext,
                "status": version.status,
            }

    @router.post(
        "/admin/v1/environments/{environment_id}/api-keys/{key_id}/versions/{version}/activate"
    )
    def post_api_key_activation(
        environment_id: str,
        key_id: str,
        version: int,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            activate_api_key_version(
                session,
                principal=principal,
                environment_public_id=environment_id,
                key_public_id=key_id,
                version_number=version,
            )
        return {"version": version, "status": "ACTIVE"}

    @router.post("/admin/v1/environments/{environment_id}/api-keys/{key_id}/revoke")
    def post_api_key_revocation(
        environment_id: str,
        key_id: str,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, str]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            revoke_api_key(
                session,
                principal=principal,
                environment_public_id=environment_id,
                key_public_id=key_id,
            )
        return {"id": key_id, "status": "REVOKED"}

    @router.patch("/admin/v1/environments/{environment_id}/api-keys/{key_id}/scopes")
    def patch_api_key_scopes(
        environment_id: str,
        key_id: str,
        payload: APIKeyScopesUpdate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            key = set_api_key_scopes(
                session,
                principal=principal,
                environment_public_id=environment_id,
                key_public_id=key_id,
                scopes=payload.scopes,
            )
            return {"id": key.public_id, "scopes": key.scopes}

    @router.post("/demo/scenarios", status_code=201)
    def create_scenario(
        payload: ScenarioCreate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        require_organisation_admin(principal)
        result = run_lost_capture_scenario(
            session_factory,
            organisation_id=principal.organisation_id,
            settings=settings,
            provider_transport=provider_transport,
            fault_controller=fault_controller,
            webhook_transport=webhook_transport,
        )
        return asdict(result)

    @router.get("/demo/scenarios/{scenario_run_id}")
    def get_scenario(scenario_run_id: str, principal: PrincipalDep) -> dict[str, object]:
        return asdict(
            read_scenario_run(
                session_factory,
                organisation_id=principal.organisation_id,
                scenario_run_id=scenario_run_id,
            )
        )

    @router.get("/v1/webhook_deliveries/{delivery_id}")
    def get_delivery(delivery_id: str, principal: PrincipalDep) -> dict[str, object]:
        return read_delivery(
            session_factory,
            organisation_id=principal.organisation_id,
            delivery_public_id=delivery_id,
        )

    @router.post("/v1/webhook_deliveries/{delivery_id}/replay", status_code=202)
    def post_delivery_replay(
        delivery_id: str,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, str]:
        require_csrf(principal, csrf_token)
        require_organisation_admin(principal)
        replay_id = replay_delivery(
            session_factory,
            organisation_id=principal.organisation_id,
            delivery_public_id=delivery_id,
        )
        return {"deliveryId": replay_id, "status": "PENDING"}

    return router
