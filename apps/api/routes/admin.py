from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from relaypay.config import Settings
from relaypay.connectors.adapters import BankConnectorAdapter, PaymentConnectorAdapter
from relaypay.connectors.service import (
    activate_connector_version,
    create_connector_version,
    verify_connector_version,
)
from relaypay.contracts import EmptyCommand
from relaypay.demo_scenarios.service import (
    ScenarioFaultController,
    read_scenario_run,
    run_lost_capture_scenario,
)
from relaypay.event_delivery.admin import read_delivery, replay_delivery
from relaypay.event_delivery.delivery import WebhookTransport
from relaypay.idempotency import build_fingerprint
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
from relaypay.merchant_balances.service import (
    create_admin_merchant_account,
    list_admin_merchant_accounts,
    list_balance_transactions,
    read_admin_balances,
    run_settlement,
)
from relaypay.operations.service import list_operations_resource
from relaypay.payouts.service import (
    create_beneficiary,
    create_payout,
    create_retry,
    list_beneficiaries,
    list_payouts,
)
from relaypay.provider_operations.service import ProviderTransport
from relaypay.reconciliation.service import (
    MAX_STATEMENT_BYTES,
    acknowledge_mismatch,
    import_statement,
    list_mismatches,
    refresh_mismatch_evidence,
    resolve_mismatch,
)
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


class MismatchNote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str = Field(min_length=1, max_length=1000)


class MismatchResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str = Field(min_length=1, max_length=1000)
    compensating_journal_id: str | None = Field(
        default=None, alias="compensatingJournalId", min_length=1, max_length=64
    )


class MerchantAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)


class BeneficiaryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str = Field(min_length=1, max_length=128)
    display_name: str = Field(alias="displayName", min_length=1, max_length=128)
    bank_account_reference: str = Field(alias="bankAccountReference", min_length=1, max_length=128)


class PayoutCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    merchant_account_id: str = Field(alias="merchantAccountId", min_length=1, max_length=64)
    beneficiary_id: str = Field(alias="beneficiaryId", min_length=1, max_length=64)
    amount: int = Field(strict=True, gt=0)
    currency: Literal["INR"]


class ConnectorVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str = Field(min_length=1, max_length=128)
    kind: Literal["PAYMENT", "BANK", "COMMERCE"]
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=512)
    capabilities: list[str] = Field(min_length=1, max_length=16)
    timeout_ms: int = Field(alias="timeoutMs", ge=100, le=30000)
    credential_name: str = Field(
        default="api_secret", alias="credentialName", min_length=1, max_length=64
    )


class ConnectorVerify(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["PAYMENT", "BANK"]


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

    @router.get("/admin/v1/environments/{environment_id}/operations/{resource}")
    def get_operations_resource(
        environment_id: str,
        resource: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        after: str | None = None,
    ) -> JSONResponse:
        with session_factory() as session, session.begin():
            page = list_operations_resource(
                session,
                principal=principal,
                environment_public_id=environment_id,
                resource=resource,
                limit=limit,
                after=after,
                cursor_secret=settings.API_KEY_PEPPER.get_secret_value(),
            )
        return JSONResponse(content={"data": page.data, "nextCursor": page.next_cursor})

    @router.get("/admin/v1/environments/{environment_id}/merchant-accounts")
    def get_merchant_accounts(
        environment_id: str, principal: PrincipalDep
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": item.public_id,
                    "reference": item.reference,
                    "name": item.name,
                    "currency": item.currency,
                    "isDefault": item.is_default,
                    "status": item.status,
                }
                for item in list_admin_merchant_accounts(
                    session,
                    principal=principal,
                    environment_public_id=environment_id,
                )
            ]

    @router.post("/admin/v1/environments/{environment_id}/merchant-accounts", status_code=201)
    def post_merchant_account(
        environment_id: str,
        payload: MerchantAccountCreate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            item = create_admin_merchant_account(
                session,
                principal=principal,
                environment_public_id=environment_id,
                reference=payload.reference,
                name=payload.name,
            )
            return {
                "id": item.public_id,
                "reference": item.reference,
                "name": item.name,
                "currency": item.currency,
                "isDefault": item.is_default,
                "status": item.status,
            }

    @router.get(
        "/admin/v1/environments/{environment_id}/merchant-accounts/{merchant_account_id}/balances"
    )
    def get_merchant_account_balances(
        environment_id: str,
        merchant_account_id: str,
        principal: PrincipalDep,
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            merchant, balances = read_admin_balances(
                session,
                principal=principal,
                environment_public_id=environment_id,
                merchant_public_id=merchant_account_id,
            )
            return {
                "merchantAccountId": merchant.public_id,
                "currency": merchant.currency,
                "pending": balances.pending,
                "available": balances.available,
                "reserved": balances.reserved,
                "receivable": balances.receivable,
                "payoutEligible": balances.payout_eligible,
            }

    @router.get(
        "/admin/v1/environments/{environment_id}/merchant-accounts/"
        "{merchant_account_id}/balance-transactions"
    )
    def get_merchant_account_balance_transactions(
        environment_id: str,
        merchant_account_id: str,
        principal: PrincipalDep,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": item.public_id,
                    "journalId": str(item.journal_id),
                    "type": item.transaction_type,
                    "pendingDelta": item.pending_delta,
                    "availableDelta": item.available_delta,
                    "receivableDelta": item.receivable_delta,
                    "payoutClearingDelta": item.payout_clearing_delta,
                    "currency": item.currency,
                    "createdAt": item.created_at.isoformat(),
                }
                for item in list_balance_transactions(
                    session,
                    principal=principal,
                    environment_public_id=environment_id,
                    merchant_public_id=merchant_account_id,
                )
            ]

    @router.post(
        "/admin/v1/environments/{environment_id}/merchant-accounts/"
        "{merchant_account_id}/settlements"
    )
    def post_merchant_account_settlement(
        environment_id: str,
        merchant_account_id: str,
        payload: EmptyCommand,
        principal: PrincipalDep,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Response:
        require_csrf(principal, csrf_token)
        fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template=(
                "/environments/{environment_id}/merchant-accounts/{merchant_account_id}/settlements"
            ),
            path_params={
                "environment_id": environment_id,
                "merchant_account_id": merchant_account_id,
            },
            body=payload,
        )
        result = run_settlement(
            session_factory,
            principal=principal,
            environment_public_id=environment_id,
            merchant_public_id=merchant_account_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            key_pepper=settings.API_KEY_PEPPER.get_secret_value(),
        )
        headers = {"Content-Type": "application/json"}
        if result.replayed:
            headers["Idempotency-Replayed"] = "true"
        return Response(content=result.body, status_code=result.status_code, headers=headers)

    @router.get("/admin/v1/environments/{environment_id}/beneficiaries")
    def get_beneficiaries(environment_id: str, principal: PrincipalDep) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": item.public_id,
                    "reference": item.reference,
                    "displayName": item.display_name,
                    "bankAccountReference": item.bank_account_reference,
                    "currency": item.currency,
                    "status": item.status,
                }
                for item in list_beneficiaries(
                    session,
                    principal=principal,
                    environment_public_id=environment_id,
                )
            ]

    @router.post("/admin/v1/environments/{environment_id}/beneficiaries", status_code=201)
    def post_beneficiary(
        environment_id: str,
        payload: BeneficiaryCreate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            item = create_beneficiary(
                session,
                principal=principal,
                environment_public_id=environment_id,
                reference=payload.reference,
                display_name=payload.display_name,
                bank_account_reference=payload.bank_account_reference,
            )
            return {
                "id": item.public_id,
                "reference": item.reference,
                "displayName": item.display_name,
                "bankAccountReference": item.bank_account_reference,
                "currency": item.currency,
                "status": item.status,
            }

    @router.get("/admin/v1/environments/{environment_id}/payouts")
    def get_payouts(environment_id: str, principal: PrincipalDep) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": item.public_id,
                    "amount": item.amount,
                    "currency": item.currency,
                    "status": item.status,
                    "failureCode": item.failure_code,
                    "reviewReason": item.review_reason,
                }
                for item in list_payouts(
                    session,
                    principal=principal,
                    environment_public_id=environment_id,
                )
            ]

    @router.post("/admin/v1/environments/{environment_id}/payouts")
    def post_payout(
        environment_id: str,
        payload: PayoutCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Response:
        require_csrf(principal, csrf_token)
        fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template="/environments/{environment_id}/payouts",
            path_params={"environment_id": environment_id},
            body=payload,
        )
        result = create_payout(
            session_factory,
            principal=principal,
            environment_public_id=environment_id,
            merchant_public_id=payload.merchant_account_id,
            beneficiary_public_id=payload.beneficiary_id,
            amount=payload.amount,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
        )
        headers = {"Content-Type": "application/json"}
        if result.replayed:
            headers["Idempotency-Replayed"] = "true"
        return Response(content=result.body, status_code=result.status_code, headers=headers)

    @router.post("/admin/v1/environments/{environment_id}/payouts/{payout_id}/attempts")
    def post_payout_retry(
        environment_id: str,
        payout_id: str,
        payload: EmptyCommand,
        principal: PrincipalDep,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> Response:
        require_csrf(principal, csrf_token)
        fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template="/environments/{environment_id}/payouts/{payout_id}/attempts",
            path_params={"environment_id": environment_id, "payout_id": payout_id},
            body=payload,
        )
        result = create_retry(
            session_factory,
            principal=principal,
            environment_public_id=environment_id,
            payout_public_id=payout_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
        )
        headers = {"Content-Type": "application/json"}
        if result.replayed:
            headers["Idempotency-Replayed"] = "true"
        return Response(content=result.body, status_code=result.status_code, headers=headers)

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

    @router.get("/admin/v1/environments/{environment_id}/reconciliation-mismatches")
    def get_reconciliation_mismatches(
        environment_id: str,
        principal: PrincipalDep,
        status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"] | None = None,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": mismatch.public_id,
                    "type": mismatch.mismatch_type,
                    "status": mismatch.workflow_status,
                    "acknowledgementNote": mismatch.acknowledgement_note,
                    "resolutionNote": mismatch.resolution_note,
                    "compensatingJournalId": (
                        str(mismatch.compensating_journal_id)
                        if mismatch.compensating_journal_id
                        else None
                    ),
                }
                for mismatch in list_mismatches(
                    session,
                    principal=principal,
                    environment_public_id=environment_id,
                    workflow_status=status,
                )
            ]

    @router.post(
        "/admin/v1/environments/{environment_id}/reconciliation-mismatches/"
        "{mismatch_id}/acknowledge"
    )
    def post_mismatch_acknowledgement(
        environment_id: str,
        mismatch_id: str,
        payload: MismatchNote,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, str]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            mismatch = acknowledge_mismatch(
                session,
                principal=principal,
                environment_public_id=environment_id,
                mismatch_public_id=mismatch_id,
                note=payload.note,
            )
            return {"id": mismatch.public_id, "status": mismatch.workflow_status}

    @router.post(
        "/admin/v1/environments/{environment_id}/reconciliation-mismatches/{mismatch_id}/resolve"
    )
    def post_mismatch_resolution(
        environment_id: str,
        mismatch_id: str,
        payload: MismatchResolution,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, str]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            mismatch = resolve_mismatch(
                session,
                principal=principal,
                environment_public_id=environment_id,
                mismatch_public_id=mismatch_id,
                note=payload.note,
                compensating_journal_public_id=payload.compensating_journal_id,
            )
            return {"id": mismatch.public_id, "status": mismatch.workflow_status}

    @router.post(
        "/admin/v1/environments/{environment_id}/reconciliation-mismatches/"
        "{mismatch_id}/evidence-versions",
        status_code=201,
    )
    def post_mismatch_evidence_version(
        environment_id: str,
        mismatch_id: str,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            version = refresh_mismatch_evidence(
                session,
                principal=principal,
                environment_public_id=environment_id,
                mismatch_public_id=mismatch_id,
            )
            return {
                "version": version.version,
                "sha256": version.evidence_sha256.hex(),
                "evidence": version.evidence,
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
            principal=principal,
            delivery_public_id=delivery_id,
        )
        return {"deliveryId": replay_id, "status": "PENDING"}

    @router.post(
        "/admin/v1/environments/{environment_id}/connector-versions",
        status_code=201,
    )
    def post_connector_version(
        environment_id: str,
        payload: ConnectorVersionCreate,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            issued = create_connector_version(
                session,
                principal=principal,
                environment_public_id=environment_id,
                reference=payload.reference,
                kind=payload.kind,
                base_url=payload.base_url,
                capabilities=payload.capabilities,
                timeout_ms=payload.timeout_ms,
                encryption_key=settings.CONNECTOR_CREDENTIAL_ENCRYPTION_KEY.get_secret_value(),
                credential_name=payload.credential_name,
            )
        return {
            "connectorId": issued.connector_public_id,
            "versionId": issued.version_public_id,
            "version": issued.version,
            "credential": issued.credential,
        }

    @router.post(
        "/admin/v1/environments/{environment_id}/connector-versions/{version_id}/verify",
        status_code=204,
    )
    def post_connector_verify(
        environment_id: str,
        version_id: str,
        payload: ConnectorVerify,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> None:
        require_csrf(principal, csrf_token)
        adapter = (
            PaymentConnectorAdapter(
                base_url=settings.PROVIDER_BASE_URL,
                signing_secret=settings.PROVIDER_SIGNING_SECRET.get_secret_value(),
                timeout_seconds=2,
            )
            if payload.kind == "PAYMENT"
            else BankConnectorAdapter(
                base_url=settings.BANK_BASE_URL,
                signing_secret=settings.BANK_SIGNING_SECRET.get_secret_value(),
                timeout_seconds=2,
            )
        )
        verify_connector_version(
            session_factory,
            principal=principal,
            environment_public_id=environment_id,
            version_public_id=version_id,
            adapter=adapter,
        )

    @router.post(
        "/admin/v1/environments/{environment_id}/connector-versions/{version_id}/activate",
        status_code=204,
    )
    def post_connector_activate(
        environment_id: str,
        version_id: str,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> None:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            activate_connector_version(
                session,
                principal=principal,
                environment_public_id=environment_id,
                version_public_id=version_id,
            )

    return router
