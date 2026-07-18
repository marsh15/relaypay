from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
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
    revoke_api_key,
    rotate_api_key,
)
from relaypay.provider_operations.service import ProviderTransport
from sqlalchemy.orm import Session, sessionmaker


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scenario_type: Literal["LOST_CAPTURE_RESPONSE"] = Field(alias="scenarioType")


class APIKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)
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

    @router.post("/demo/scenarios", status_code=201)
    def create_scenario(
        payload: ScenarioCreate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
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
        replay_id = replay_delivery(
            session_factory,
            organisation_id=principal.organisation_id,
            delivery_public_id=delivery_id,
        )
        return {"deliveryId": replay_id, "status": "PENDING"}

    return router
