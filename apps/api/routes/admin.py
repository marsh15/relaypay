import hashlib
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from relaypay.agent_runtime.contracts import StructuredModelProvider
from relaypay.agent_runtime.models import ApprovalRequest
from relaypay.agent_runtime.workflows import (
    decide_approval,
    list_runs,
    read_run,
    resolve_admin_scope,
)
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
from relaypay.disputes.evidence import draft_from_allowlisted_evidence
from relaypay.disputes.models import DisputePackageVersion
from relaypay.disputes.network import DeterministicDisputeNetwork
from relaypay.disputes.service import (
    DisputeNetwork,
    StructuredDisputeDraft,
    create_draft,
    freeze_draft,
    latest_draft_for_admin,
    list_cases,
    mark_package_approved,
    read_case,
    read_package_for_admin,
    submit_approved_package,
)
from relaypay.errors import not_found
from relaypay.event_delivery.admin import read_delivery, replay_delivery
from relaypay.event_delivery.delivery import WebhookTransport
from relaypay.idempotency import build_fingerprint, canonical_json_bytes
from relaypay.identity.security import Principal, verify_csrf
from relaypay.identity.service import (
    activate_api_key_version,
    append_audit,
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
from relaypay.merchant_balances.models import (
    MerchantAccount,
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
from relaypay.risk_review.models import (
    RiskEscalation,
    RiskReview,
)
from relaypay.risk_review.service import (
    annotate_review,
    disposition_escalation,
    execute_review,
    list_reviews,
    prepare_review,
    read_review_payload,
)
from relaypay.risk_review.snapshot import (
    DeterministicSiteSnapshotSource,
    HTTPSiteSnapshotSource,
    SiteSnapshotSource,
)
from relaypay.settlement_intelligence.models import (
    SettlementForecast,
    SettlementForecastItem,
    SettlementPolicy,
    SettlementQuestion,
)
from relaypay.settlement_intelligence.provider import SettlementFakeProvider
from relaypay.settlement_intelligence.service import (
    answer_question,
    create_policy,
    policy_window,
    record_pre_cutoff_forecast,
)
from relaypay.settlement_intelligence.windows import PolicyWindow, format_inr
from relaypay.subscriptions.execution import (
    CommunicationNetwork,
    RecurringPaymentNetwork,
    execute_message_action,
    execute_payment_action,
)
from relaypay.subscriptions.intake import (
    RecurringPaymentFailedEnvelope,
    consume_recurring_payment_failed,
)
from relaypay.subscriptions.models import (
    RecoveryCase,
    ScheduledRecoveryAction,
    Subscription,
    SubscriptionInvoice,
)
from relaypay.subscriptions.network import (
    DeterministicCommunicationNetwork,
    DeterministicRecurringPaymentNetwork,
)
from relaypay.subscriptions.service import (
    create_invoice,
    create_subscription,
    list_recovery_cases,
    read_recovery_case,
    record_opt_out,
)
from sqlalchemy import select
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
    role: Literal["ORGANISATION_ADMIN", "DEVELOPER", "VIEWER", "OPERATIONS_ANALYST", "APPROVER"]
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


class ApprovalDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["APPROVED", "REJECTED"]
    note: str | None = Field(default=None, max_length=1000)


class SubscriptionConsent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channels: list[Literal["EMAIL", "WHATSAPP", "IN_APP"]] = Field(max_length=3)
    display_name: str = Field(alias="displayName", min_length=1, max_length=128)


class SubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    customer_id: str = Field(alias="customerId", pattern=r"^cus_[0-9a-f]{32}$")
    external_id: str = Field(alias="externalId", min_length=1, max_length=128)
    plan_reference: str = Field(alias="planReference", min_length=1, max_length=128)
    amount: int = Field(gt=0)
    consent: SubscriptionConsent


class SubscriptionInvoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    external_id: str = Field(alias="externalId", min_length=1, max_length=128)
    due_at: AwareDatetime = Field(alias="dueAt")


class RecurringFailureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subscription_id: str = Field(alias="subscriptionId", pattern=r"^sub_[0-9a-f]{32}$")
    provider_attempt_id: str = Field(alias="providerAttemptId", min_length=1, max_length=128)
    provider_code: str = Field(alias="providerCode", min_length=1, max_length=64)
    outcome: Literal["VERIFIED_FAILED", "TRANSPORT_UNKNOWN"]
    evidence: dict[str, object]


class RecoveryOptOutCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_event_id: str = Field(alias="sourceEventId", pattern=r"^bev_[0-9a-f]{32}$")
    channel: Literal["ALL", "EMAIL", "WHATSAPP", "IN_APP"] = "ALL"


class SettlementPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    merchant_account_id: str = Field(alias="merchantAccountId", min_length=1, max_length=64)
    timezone: str = Field(min_length=1, max_length=64)
    cutoff_hour: int = Field(alias="cutoffHour", ge=0, le=23)
    cutoff_minute: int = Field(alias="cutoffMinute", ge=0, le=59)
    settlement_delay_days: int = Field(alias="settlementDelayDays", ge=0, le=2)
    weekend_handling: Literal["INCLUDE", "SKIP"] = Field(alias="weekendHandling")


class SettlementQuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=2000)
    merchant_account_id: str | None = Field(
        default=None, alias="merchantAccountId", min_length=1, max_length=64
    )


class RiskSnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    site_ref: Literal[
        "COMPLETE_CLEAN",
        "MISSING_POLICIES",
        "YOUNG_DOMAIN",
        "PRICE_OUTLIER",
        "SUSPICIOUS_CLAIMS",
        "PROHIBITED_CATEGORY",
    ] = Field(alias="siteRef")


class RiskReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    site_ref: Literal[
        "COMPLETE_CLEAN",
        "MISSING_POLICIES",
        "YOUNG_DOMAIN",
        "PRICE_OUTLIER",
        "SUSPICIOUS_CLAIMS",
        "PROHIBITED_CATEGORY",
    ] = Field(alias="siteRef")


class RiskAnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str = Field(min_length=1, max_length=2000)


class RiskDispositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    disposition: Literal["REJECT_ONBOARDING", "REQUEST_DOCUMENTS", "CLOSE_NO_ACTION"]
    note: str = Field(min_length=1, max_length=2000)


def build_admin_router(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    provider_transport: ProviderTransport,
    fault_controller: ScenarioFaultController,
    webhook_transport: WebhookTransport,
    principal_dependency: Callable[..., Principal],
    dispute_network: DisputeNetwork | None = None,
    communication_network: CommunicationNetwork | None = None,
    recurring_payment_network: RecurringPaymentNetwork | None = None,
    settlement_model_provider: StructuredModelProvider | None = None,
    risk_findings_provider: StructuredModelProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["admin"])
    PrincipalDep = Annotated[Principal, Depends(principal_dependency)]
    resolved_dispute_network = dispute_network or DeterministicDisputeNetwork()
    resolved_communication_network = communication_network or DeterministicCommunicationNetwork()
    resolved_recurring_payment_network = (
        recurring_payment_network or DeterministicRecurringPaymentNetwork()
    )

    resolved_settlement_provider = settlement_model_provider or SettlementFakeProvider()

    from relaypay.agent_runtime.contracts import ModelRequest, ModelResult
    from relaypay.risk_review.findings import ModelFindings as _ModelFindings
    from relaypay.risk_review.findings import deterministic_findings

    class RiskFindingsFakeProvider:
        name = "fake"

        def generate_structured(self, request: ModelRequest) -> ModelResult:
            if request.schema is not _ModelFindings:
                from relaypay.agent_runtime.contracts import TerminalModelError

                raise TerminalModelError("unsupported risk findings schema")
            output = deterministic_findings_from_prompt(request.prompt)
            request_bytes = canonical_json_bytes(
                {"model": request.model_id, "prompt": request.prompt}
            )
            response_bytes = canonical_json_bytes(output.model_dump(mode="json"))
            return ModelResult(
                output=output,
                provider=self.name,
                model_id=request.model_id,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                latency_ms=0,
                input_tokens=max(1, len(request.prompt) // 4),
                output_tokens=max(1, len(response_bytes) // 4),
                finish_status="STOP",
            )

    def deterministic_findings_from_prompt(prompt: str) -> _ModelFindings:
        # The prompt embeds the delimited snapshot JSON; extract it deterministically.
        marker = "<relaypay-untrusted-evidence>\n"
        start = prompt.index(marker) + len(marker)
        end = prompt.index("\n</relaypay-untrusted-evidence>", start)
        import json as _json

        snapshot = _json.loads(prompt[start:end])
        return deterministic_findings(snapshot)

    resolved_risk_provider = risk_findings_provider or RiskFindingsFakeProvider()

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

    @router.get("/admin/v1/environments/{environment_id}/workflow-runs")
    def get_workflow_runs(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            items = list_runs(
                session,
                principal=principal,
                environment_public_id=environment_id,
                limit=limit,
            )
            return [
                {
                    "id": item.public_id,
                    "status": item.status,
                    "route": item.route,
                    "tokensUsed": item.tokens_used,
                    "tokenBudget": item.token_budget,
                    "costUsedUsdMicros": item.cost_used_usd_micros,
                    "costBudgetUsdMicros": item.cost_budget_usd_micros,
                    "createdAt": item.created_at.isoformat(),
                }
                for item in items
            ]

    @router.get("/admin/v1/environments/{environment_id}/workflow-runs/{run_id}")
    def get_workflow_run(
        environment_id: str, run_id: str, principal: PrincipalDep
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            run, steps, artifacts, approvals, dead_letters = read_run(
                session,
                principal=principal,
                environment_public_id=environment_id,
                run_public_id=run_id,
            )
            return {
                "id": run.public_id,
                "status": run.status,
                "route": run.route,
                "steps": [
                    {
                        "id": item.public_id,
                        "key": item.step_key,
                        "kind": item.step_kind,
                        "status": item.status,
                        "attemptCount": item.attempt_count,
                        "safeErrorCode": item.safe_error_code,
                    }
                    for item in steps
                ],
                "artifacts": [
                    {
                        "id": item.public_id,
                        "type": item.artifact_type,
                        "version": item.version,
                        "mediaType": item.media_type,
                        "sha256": item.content_sha256.hex(),
                        "byteLength": item.byte_length,
                    }
                    for item in artifacts
                ],
                "approvals": [
                    {
                        "id": item.public_id,
                        "artifactSha256": item.artifact_sha256.hex(),
                        "status": item.status,
                    }
                    for item in approvals
                ],
                "deadLetters": [
                    {
                        "id": item.public_id,
                        "reasonCode": item.reason_code,
                        "replayCount": item.replay_count,
                        "evidence": item.evidence,
                    }
                    for item in dead_letters
                ],
            }

    @router.post(
        "/admin/v1/environments/{environment_id}/approval-requests/{request_id}/decisions",
        status_code=201,
    )
    def post_approval_decision(
        environment_id: str,
        request_id: str,
        payload: ApprovalDecisionCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, str]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            item = decide_approval(
                session,
                principal=principal,
                environment_public_id=environment_id,
                request_public_id=request_id,
                decision=payload.decision,
                note=payload.note,
            )
            if payload.decision == "APPROVED":
                approval = session.get(ApprovalRequest, item.approval_request_id)
                if approval is not None:
                    package = session.scalar(
                        select(DisputePackageVersion).where(
                            DisputePackageVersion.workflow_artifact_id == approval.artifact_id,
                            DisputePackageVersion.status == "FROZEN",
                        )
                    )
                    if package is not None:
                        mark_package_approved(
                            session, package_public_id=package.public_id, approval=approval
                        )
            return {"id": item.public_id, "decision": item.decision}

    @router.get("/admin/v1/environments/{environment_id}/disputes")
    def get_disputes(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": case.public_id,
                    "networkDisputeId": case.network_dispute_id,
                    "reasonCode": case.reason_code,
                    "amount": case.amount,
                    "currency": case.currency,
                    "status": case.status,
                    "dueAt": case.due_at.isoformat(),
                    "createdAt": case.created_at.isoformat(),
                }
                for case in list_cases(
                    session,
                    principal=principal,
                    environment_public_id=environment_id,
                    limit=limit,
                )
            ]

    @router.get("/admin/v1/environments/{environment_id}/disputes/{case_id}")
    def get_dispute(
        environment_id: str, case_id: str, principal: PrincipalDep
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            case, drafts, packages = read_case(
                session,
                principal=principal,
                environment_public_id=environment_id,
                case_public_id=case_id,
            )
            approvals = {
                item.artifact_id: item
                for item in session.scalars(
                    select(ApprovalRequest).where(
                        ApprovalRequest.workflow_run_id == case.workflow_run_id
                    )
                ).all()
            }
            return {
                "id": case.public_id,
                "networkDisputeId": case.network_dispute_id,
                "reasonCode": case.reason_code,
                "amount": case.amount,
                "currency": case.currency,
                "status": case.status,
                "dueAt": case.due_at.isoformat(),
                "sourceSha256": case.source_sha256.hex(),
                "drafts": [
                    {
                        "id": draft.public_id,
                        "version": draft.version,
                        "authorType": draft.author_type,
                        "classification": draft.classification,
                        "confidence": draft.confidence,
                        "responseText": draft.response_text,
                        "selectedEvidence": draft.selected_evidence,
                        "missingEvidence": draft.missing_evidence,
                        "sha256": draft.content_sha256.hex(),
                    }
                    for draft in drafts
                ],
                "packages": [
                    {
                        "id": package.public_id,
                        "version": package.version,
                        "status": package.status,
                        "sha256": package.package_sha256.hex(),
                        "byteLength": package.byte_length,
                        "approvalRequestId": (
                            approvals[package.workflow_artifact_id].public_id
                            if package.workflow_artifact_id in approvals
                            else None
                        ),
                        "approvalStatus": (
                            approvals[package.workflow_artifact_id].status
                            if package.workflow_artifact_id in approvals
                            else None
                        ),
                    }
                    for package in packages
                ],
            }

    @router.post(
        "/admin/v1/environments/{environment_id}/disputes/{case_id}/agent-drafts",
        status_code=201,
    )
    def post_agent_dispute_draft(
        environment_id: str,
        case_id: str,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            case, _, _ = read_case(
                session,
                principal=principal,
                environment_public_id=environment_id,
                case_public_id=case_id,
                permission="workflows:write",
            )
            item = create_draft(
                session,
                case=case,
                draft=draft_from_allowlisted_evidence(case),
                author_type="AGENT",
                author_user_id=None,
            )
            append_audit(
                session,
                principal=principal,
                environment_id=case.environment_id,
                action="DISPUTE_DRAFT_CREATED",
                target_type="DISPUTE_DRAFT",
                target_id=item.public_id,
            )
            return {"id": item.public_id, "version": item.version}

    @router.post(
        "/admin/v1/environments/{environment_id}/disputes/{case_id}/drafts",
        status_code=201,
    )
    def post_analyst_dispute_draft(
        environment_id: str,
        case_id: str,
        payload: StructuredDisputeDraft,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        if principal.user_id is None:
            raise ValueError("session user required")
        with session_factory() as session, session.begin():
            case, _ = latest_draft_for_admin(
                session,
                principal=principal,
                environment_public_id=environment_id,
                case_public_id=case_id,
            )
            item = create_draft(
                session,
                case=case,
                draft=payload,
                author_type="ANALYST",
                author_user_id=principal.user_id,
            )
            append_audit(
                session,
                principal=principal,
                environment_id=case.environment_id,
                action="DISPUTE_DRAFT_EDITED",
                target_type="DISPUTE_DRAFT",
                target_id=item.public_id,
            )
            return {"id": item.public_id, "version": item.version}

    @router.post(
        "/admin/v1/environments/{environment_id}/disputes/{case_id}/packages",
        status_code=201,
    )
    def post_dispute_package(
        environment_id: str,
        case_id: str,
        principal: PrincipalDep,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        if principal.user_id is None:
            raise ValueError("session user required")
        with session_factory() as session, session.begin():
            case, draft = latest_draft_for_admin(
                session,
                principal=principal,
                environment_public_id=environment_id,
                case_public_id=case_id,
            )
            package, approval = freeze_draft(
                session,
                case=case,
                draft=draft,
                attachments=(),
                signing_secret=settings.DISPUTE_PACKAGE_SIGNING_SECRET.get_secret_value().encode(),
                maker_user_id=principal.user_id,
            )
            append_audit(
                session,
                principal=principal,
                environment_id=case.environment_id,
                action="DISPUTE_PACKAGE_FROZEN",
                target_type="DISPUTE_PACKAGE",
                target_id=package.public_id,
                details={"sha256": package.package_sha256.hex()},
            )
            return {
                "id": package.public_id,
                "sha256": package.package_sha256.hex(),
                "approvalRequestId": approval.public_id,
            }

    @router.get("/admin/v1/environments/{environment_id}/dispute-packages/{package_id}/download")
    def get_dispute_package(
        environment_id: str, package_id: str, principal: PrincipalDep
    ) -> Response:
        with session_factory() as session, session.begin():
            package = read_package_for_admin(
                session,
                principal=principal,
                environment_public_id=environment_id,
                package_public_id=package_id,
            )
            content = bytes(package.package_bytes)
            digest = package.package_sha256.hex()
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{package_id}.zip"',
                "X-Content-SHA256": digest,
            },
        )

    @router.post(
        "/admin/v1/environments/{environment_id}/dispute-packages/{package_id}/submit",
        status_code=202,
    )
    def post_dispute_submission(
        environment_id: str,
        package_id: str,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            package = read_package_for_admin(
                session,
                principal=principal,
                environment_public_id=environment_id,
                package_public_id=package_id,
                permission="workflows:write",
            )
            resolved_package_id = package.public_id
        attempt = submit_approved_package(
            session_factory,
            package_public_id=resolved_package_id,
            network=resolved_dispute_network,
        )
        return {"id": attempt.public_id, "status": attempt.status}

    @router.get("/admin/v1/environments/{environment_id}/subscriptions")
    def get_subscriptions(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="financial:read",
            )
            items = session.scalars(
                select(Subscription)
                .where(
                    Subscription.organisation_id == organisation_id,
                    Subscription.environment_id == resolved_environment_id,
                )
                .order_by(Subscription.created_at.desc(), Subscription.id.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": item.public_id,
                    "externalId": item.external_id,
                    "planReference": item.plan_reference,
                    "amount": item.amount,
                    "currency": item.currency,
                    "status": item.status,
                    "consentSha256": item.consent_sha256.hex(),
                    "createdAt": item.created_at.isoformat(),
                }
                for item in items
            ]

    @router.post("/admin/v1/environments/{environment_id}/subscriptions", status_code=201)
    def post_subscription(
        environment_id: str,
        payload: SubscriptionCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            item = create_subscription(
                session,
                organisation_id=organisation_id,
                environment_id=resolved_environment_id,
                customer_public_id=payload.customer_id,
                external_id=payload.external_id,
                plan_reference=payload.plan_reference,
                amount=payload.amount,
                consent=payload.consent.model_dump(mode="json", by_alias=True),
            )
            return {"id": item.public_id, "status": item.status}

    @router.post(
        "/admin/v1/environments/{environment_id}/subscriptions/{subscription_id}/invoices",
        status_code=201,
    )
    def post_subscription_invoice(
        environment_id: str,
        subscription_id: str,
        payload: SubscriptionInvoiceCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            subscription = session.scalar(
                select(Subscription).where(
                    Subscription.organisation_id == organisation_id,
                    Subscription.environment_id == resolved_environment_id,
                    Subscription.public_id == subscription_id,
                )
            )
            if subscription is None:
                raise not_found("Subscription")
            item = create_invoice(
                session,
                subscription=subscription,
                external_id=payload.external_id,
                due_at=datetime.fromisoformat(payload.due_at.isoformat()),
            )
            return {"id": item.public_id, "status": item.status}

    @router.post(
        "/admin/v1/environments/{environment_id}/subscription-invoices/{invoice_id}/failures",
        status_code=202,
    )
    def post_recurring_failure(
        environment_id: str,
        invoice_id: str,
        payload: RecurringFailureCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        now = datetime.now(UTC)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            invoice = session.scalar(
                select(SubscriptionInvoice).where(
                    SubscriptionInvoice.organisation_id == organisation_id,
                    SubscriptionInvoice.environment_id == resolved_environment_id,
                    SubscriptionInvoice.public_id == invoice_id,
                )
            )
            if invoice is None:
                raise not_found("Subscription invoice")
            event_payload = {
                "subscriptionId": payload.subscription_id,
                "invoiceId": invoice.public_id,
                "providerAttemptId": payload.provider_attempt_id,
                "providerCode": payload.provider_code,
                "outcome": payload.outcome,
                "evidence": payload.evidence,
            }
            event_hex = hashlib.sha256(
                f"{organisation_id}:{resolved_environment_id}:{idempotency_key}".encode()
            ).hexdigest()[:32]
            envelope = RecurringPaymentFailedEnvelope.model_validate(
                {
                    "eventId": f"bev_{event_hex}",
                    "eventType": "recurring-payment.failed.v1",
                    "schemaVersion": 1,
                    "occurredAt": now.isoformat(),
                    "organisationId": principal.organisation_public_id,
                    "environmentId": environment_id,
                    "resourceType": "subscription_invoice",
                    "resourceId": invoice.public_id,
                    "payload": event_payload,
                    "payloadSha256": hashlib.sha256(
                        canonical_json_bytes(event_payload)
                    ).hexdigest(),
                }
            )
            result = consume_recurring_payment_failed(session, envelope)
            return {
                "attemptId": result.attempt.public_id,
                "caseId": None if result.case is None else result.case.public_id,
                "status": "LOOKUP_REQUIRED" if result.case is None else result.case.status,
            }

    @router.get("/admin/v1/environments/{environment_id}/recovery-cases")
    def get_recovery_cases(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            return [
                {
                    "id": item.public_id,
                    "status": item.status,
                    "classification": item.classification,
                    "paymentRetryCount": item.payment_retry_count,
                    "messageCount": item.message_count,
                    "expiresAt": item.expires_at.isoformat(),
                    "createdAt": item.created_at.isoformat(),
                }
                for item in list_recovery_cases(
                    session,
                    principal=principal,
                    environment_public_id=environment_id,
                    limit=limit,
                )
            ]

    @router.get("/admin/v1/environments/{environment_id}/recovery-cases/{case_id}")
    def get_recovery_case(
        environment_id: str, case_id: str, principal: PrincipalDep
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            case, subscription, invoice, actions = read_recovery_case(
                session,
                principal=principal,
                environment_public_id=environment_id,
                case_public_id=case_id,
            )
            return {
                "id": case.public_id,
                "status": case.status,
                "classification": case.classification,
                "terminationReason": case.termination_reason,
                "subscription": {
                    "id": subscription.public_id,
                    "planReference": subscription.plan_reference,
                    "consentSha256": subscription.consent_sha256.hex(),
                },
                "invoice": {
                    "id": invoice.public_id,
                    "amount": invoice.amount,
                    "currency": invoice.currency,
                    "status": invoice.status,
                },
                "actions": [
                    {
                        "id": action.public_id,
                        "sequence": action.sequence,
                        "type": action.action_type,
                        "channel": action.channel,
                        "status": action.status,
                        "scheduledFor": action.scheduled_for.isoformat(),
                        "due": action.scheduled_for <= datetime.now(UTC),
                        "payloadSha256": action.payload_sha256.hex(),
                        "responseCode": action.response_code,
                    }
                    for action in actions
                ],
            }

    @router.post(
        "/admin/v1/environments/{environment_id}/recovery-actions/{action_id}/execute",
        status_code=202,
    )
    def post_recovery_action(
        environment_id: str,
        action_id: str,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            action = session.scalar(
                select(ScheduledRecoveryAction).where(
                    ScheduledRecoveryAction.organisation_id == organisation_id,
                    ScheduledRecoveryAction.environment_id == resolved_environment_id,
                    ScheduledRecoveryAction.public_id == action_id,
                )
            )
            if action is None:
                raise not_found("Recovery action")
            action_type = action.action_type
        item = (
            execute_message_action(
                session_factory,
                action_public_id=action_id,
                network=resolved_communication_network,
            )
            if action_type == "MESSAGE"
            else execute_payment_action(
                session_factory,
                action_public_id=action_id,
                network=resolved_recurring_payment_network,
            )
        )
        return {"id": item.public_id, "status": item.status}

    @router.post(
        "/admin/v1/environments/{environment_id}/recovery-cases/{case_id}/opt-outs",
        status_code=201,
    )
    def post_recovery_opt_out(
        environment_id: str,
        case_id: str,
        payload: RecoveryOptOutCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            case = session.scalar(
                select(RecoveryCase)
                .where(
                    RecoveryCase.organisation_id == organisation_id,
                    RecoveryCase.environment_id == resolved_environment_id,
                    RecoveryCase.public_id == case_id,
                )
                .with_for_update()
            )
            if case is None:
                raise not_found("Recovery case")
            item = record_opt_out(
                session,
                case=case,
                source_event_id=payload.source_event_id,
                channel=payload.channel,
                received_at=datetime.now(UTC),
            )
            return {"id": item.public_id, "caseStatus": case.status}

    def _resolve_settlement_account(
        session: Session,
        organisation_id: uuid.UUID,
        environment_id: uuid.UUID,
        merchant_account_public_id: str | None,
    ) -> "MerchantAccount":
        statement = select(MerchantAccount).where(
            MerchantAccount.organisation_id == organisation_id,
            MerchantAccount.environment_id == environment_id,
            MerchantAccount.status == "ACTIVE",
        )
        if merchant_account_public_id is None:
            statement = statement.where(MerchantAccount.is_default.is_(True))
        else:
            statement = statement.where(MerchantAccount.public_id == merchant_account_public_id)
        account = session.scalar(statement)
        if account is None:
            raise not_found("Merchant account")
        return account

    def _account_public_id(session: Session, account_id: uuid.UUID) -> str | None:
        value = session.scalar(
            select(MerchantAccount.public_id).where(MerchantAccount.id == account_id)
        )
        return value

    def _capture_public_id(session: Session, capture_id: uuid.UUID | None) -> str | None:
        if capture_id is None:
            return None
        from relaypay.payments.models import Capture

        return session.scalar(select(Capture.public_id).where(Capture.id == capture_id))

    def _refund_public_id(session: Session, refund_id: uuid.UUID | None) -> str | None:
        if refund_id is None:
            return None
        from relaypay.payments.models import Refund

        return session.scalar(select(Refund.public_id).where(Refund.id == refund_id))

    def _active_policy_for(
        session: Session,
        organisation_id: uuid.UUID,
        environment_id: uuid.UUID,
        merchant_account_id: uuid.UUID,
    ) -> SettlementPolicy:
        from relaypay.settlement_intelligence.service import (
            active_policy,
            ensure_default_policy,
        )

        policy = active_policy(
            session,
            organisation_id=organisation_id,
            environment_id=environment_id,
            merchant_account_id=merchant_account_id,
        )
        if policy is not None:
            return policy
        created = ensure_default_policy(
            session,
            organisation_id=organisation_id,
            environment_id=environment_id,
            merchant_account_id=merchant_account_id,
        )
        return created

    @router.get("/admin/v1/environments/{environment_id}/settlement-policies")
    def get_settlement_policies(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="financial:read",
            )
            items = session.scalars(
                select(SettlementPolicy)
                .where(
                    SettlementPolicy.organisation_id == organisation_id,
                    SettlementPolicy.environment_id == resolved_environment_id,
                )
                .order_by(SettlementPolicy.created_at.desc(), SettlementPolicy.id.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": item.public_id,
                    "merchantAccountId": _account_public_id(session, item.merchant_account_id),
                    "version": item.version,
                    "timezone": item.timezone_name,
                    "cutoff": f"{item.cutoff_hour:02d}:{item.cutoff_minute:02d}",
                    "cutoffHour": item.cutoff_hour,
                    "cutoffMinute": item.cutoff_minute,
                    "settlementDelayDays": item.settlement_delay_days,
                    "weekendHandling": item.weekend_handling,
                    "status": item.status,
                    "policySha256": item.policy_sha256.hex(),
                    "createdAt": item.created_at.isoformat(),
                }
                for item in items
            ]

    @router.post("/admin/v1/environments/{environment_id}/settlement-policies", status_code=201)
    def post_settlement_policy(
        environment_id: str,
        payload: SettlementPolicyCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            account = _resolve_settlement_account(
                session, organisation_id, resolved_environment_id, payload.merchant_account_id
            )
            item = create_policy(
                session,
                organisation_id=organisation_id,
                environment_id=resolved_environment_id,
                merchant_account_id=account.id,
                window=PolicyWindow(
                    timezone_name=payload.timezone,
                    cutoff_hour=payload.cutoff_hour,
                    cutoff_minute=payload.cutoff_minute,
                    settlement_delay_days=payload.settlement_delay_days,
                    weekend_handling=payload.weekend_handling,
                ),
            )
            return {
                "id": item.public_id,
                "version": item.version,
                "status": item.status,
                "policySha256": item.policy_sha256.hex(),
            }

    @router.post(
        "/admin/v1/environments/{environment_id}/merchant-accounts/{account_id}"
        "/settlement-forecasts",
        status_code=201,
    )
    def post_settlement_forecast(
        environment_id: str,
        account_id: str,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            account = _resolve_settlement_account(
                session, organisation_id, resolved_environment_id, account_id
            )
            policy = _active_policy_for(
                session, organisation_id, resolved_environment_id, account.id
            )
            item = record_pre_cutoff_forecast(
                session,
                organisation_id=organisation_id,
                environment_id=resolved_environment_id,
                merchant_account_id=account.id,
                policy=policy,
                now=datetime.now(UTC),
            )
            return {
                "id": item.public_id,
                "businessDate": item.business_date.isoformat(),
                "sequence": item.sequence,
                "expectedSettlementAmount": item.expected_settlement_amount,
                "expectedSettlementFormatted": format_inr(item.expected_settlement_amount),
                "captureTotal": item.capture_total,
                "refundTotal": item.refund_total,
                "receivableOffsetTotal": item.receivable_offset_total,
                "expectedArrivalDate": item.expected_arrival_date.isoformat(),
                "snapshotSha256": item.snapshot_sha256.hex(),
            }

    @router.get("/admin/v1/environments/{environment_id}/settlement-forecasts")
    def get_settlement_forecasts(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="financial:read",
            )
            items = session.scalars(
                select(SettlementForecast)
                .where(
                    SettlementForecast.organisation_id == organisation_id,
                    SettlementForecast.environment_id == resolved_environment_id,
                )
                .order_by(SettlementForecast.created_at.desc(), SettlementForecast.id.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": item.public_id,
                    "merchantAccountId": _account_public_id(session, item.merchant_account_id),
                    "businessDate": item.business_date.isoformat(),
                    "sequence": item.sequence,
                    "expectedSettlementAmount": item.expected_settlement_amount,
                    "expectedSettlementFormatted": format_inr(item.expected_settlement_amount),
                    "expectedArrivalDate": item.expected_arrival_date.isoformat(),
                    "snapshotSha256": item.snapshot_sha256.hex(),
                    "createdAt": item.created_at.isoformat(),
                }
                for item in items
            ]

    @router.get("/admin/v1/environments/{environment_id}/settlement-forecasts/{forecast_id}")
    def get_settlement_forecast(
        environment_id: str, forecast_id: str, principal: PrincipalDep
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="financial:read",
            )
            item = session.scalar(
                select(SettlementForecast).where(
                    SettlementForecast.organisation_id == organisation_id,
                    SettlementForecast.environment_id == resolved_environment_id,
                    SettlementForecast.public_id == forecast_id,
                )
            )
            if item is None:
                raise not_found("Settlement forecast")
            items = list(
                session.scalars(
                    select(SettlementForecastItem)
                    .where(SettlementForecastItem.forecast_id == item.id)
                    .order_by(SettlementForecastItem.item_type, SettlementForecastItem.created_at)
                ).all()
            )
            policy = session.get(SettlementPolicy, item.policy_id)
            window = policy_window(policy) if policy is not None else PolicyWindow.default()
            return {
                "id": item.public_id,
                "merchantAccountId": _account_public_id(session, item.merchant_account_id),
                "businessDate": item.business_date.isoformat(),
                "sequence": item.sequence,
                "cutoffAt": item.cutoff_at.isoformat(),
                "expectedArrivalDate": item.expected_arrival_date.isoformat(),
                "captureTotal": item.capture_total,
                "refundTotal": item.refund_total,
                "receivableOffsetTotal": item.receivable_offset_total,
                "expectedSettlementAmount": item.expected_settlement_amount,
                "captureCount": item.capture_count,
                "refundCount": item.refund_count,
                "snapshot": item.snapshot,
                "snapshotSha256": item.snapshot_sha256.hex(),
                "policy": {
                    "timezone": window.timezone_name,
                    "cutoff": f"{window.cutoff_hour:02d}:{window.cutoff_minute:02d}",
                    "settlementDelayDays": window.settlement_delay_days,
                    "weekendHandling": window.weekend_handling,
                },
                "items": [
                    {
                        "id": entry.public_id,
                        "type": entry.item_type,
                        "captureId": _capture_public_id(session, entry.capture_id),
                        "refundId": _refund_public_id(session, entry.refund_id),
                        "amount": entry.amount,
                    }
                    for entry in items
                ],
            }

    @router.post("/admin/v1/environments/{environment_id}/settlement-questions")
    def post_settlement_question(
        environment_id: str,
        payload: SettlementQuestionCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="financial:read",
            )
            account = _resolve_settlement_account(
                session, organisation_id, resolved_environment_id, payload.merchant_account_id
            )
            account_id = account.id
        key_digest = hashlib.sha256(
            f"settlement-question:{organisation_id}:{resolved_environment_id}"
            f":{idempotency_key}".encode()
        ).digest()
        payload_value, _replayed = answer_question(
            session_factory,
            organisation_id=organisation_id,
            environment_id=resolved_environment_id,
            merchant_account_id=account_id,
            organisation_public_id=principal.organisation_public_id,
            environment_public_id=environment_id,
            question_text=payload.question,
            idempotency_key_digest=key_digest,
            provider=resolved_settlement_provider,
        )
        return payload_value

    @router.get("/admin/v1/environments/{environment_id}/settlement-questions")
    def get_settlement_questions(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="financial:read",
            )
            items = session.scalars(
                select(SettlementQuestion)
                .where(
                    SettlementQuestion.organisation_id == organisation_id,
                    SettlementQuestion.environment_id == resolved_environment_id,
                )
                .order_by(SettlementQuestion.created_at.desc(), SettlementQuestion.id.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": item.public_id,
                    "question": item.question_text,
                    "intent": item.intent,
                    "status": item.status,
                    "askedAt": item.asked_at.isoformat(),
                    "createdAt": item.created_at.isoformat(),
                }
                for item in items
            ]

    @router.get("/admin/v1/environments/{environment_id}/settlement-questions/{question_id}")
    def get_settlement_question(
        environment_id: str, question_id: str, principal: PrincipalDep
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="financial:read",
            )
            item = session.scalar(
                select(SettlementQuestion).where(
                    SettlementQuestion.organisation_id == organisation_id,
                    SettlementQuestion.environment_id == resolved_environment_id,
                    SettlementQuestion.public_id == question_id,
                )
            )
            if item is None:
                raise not_found("Settlement question")
            return {
                "id": item.public_id,
                "question": item.question_text,
                "intent": item.intent,
                "status": item.status,
                "classification": item.classification,
                "answer": item.answer,
                "answerSha256": item.answer_sha256.hex() if item.answer_sha256 else None,
                "askedAt": item.asked_at.isoformat(),
                "answeredAt": item.answered_at.isoformat() if item.answered_at else None,
            }

    def _risk_site_source() -> SiteSnapshotSource:
        base_url = settings.RISK_SITE_BASE_URL
        if base_url:
            return HTTPSiteSnapshotSource(base_url)
        return DeterministicSiteSnapshotSource()

    @router.get("/admin/v1/environments/{environment_id}/risk-reviews")
    def get_risk_reviews(
        environment_id: str,
        principal: PrincipalDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, object]]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="technical:read",
            )
            return [
                {
                    "id": review.public_id,
                    "siteRef": site_ref,
                    "status": review.status,
                    "scoreVersion": review.score_version,
                    "createdAt": review.created_at.isoformat(),
                }
                for review, site_ref in list_reviews(
                    session,
                    organisation_id=organisation_id,
                    environment_id=resolved_environment_id,
                    limit=limit,
                )
            ]

    @router.post("/admin/v1/environments/{environment_id}/risk-reviews")
    def post_risk_review(
        environment_id: str,
        payload: RiskReviewCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
        prepared = prepare_review(
            session_factory,
            organisation_id=organisation_id,
            environment_id=resolved_environment_id,
            site_ref=payload.site_ref,
            source=_risk_site_source(),
            source_url=str(settings.RISK_SITE_BASE_URL),
        )
        return execute_review(
            session_factory,
            prepared,
            provider=resolved_risk_provider,
            now=datetime.now(UTC),
        )

    @router.get("/admin/v1/environments/{environment_id}/risk-reviews/{review_id}")
    def get_risk_review(
        environment_id: str, review_id: str, principal: PrincipalDep
    ) -> dict[str, object]:
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="technical:read",
            )
        return read_review_payload(
            session_factory,
            review_id,
            organisation_id=organisation_id,
            environment_id=resolved_environment_id,
        )

    @router.post(
        "/admin/v1/environments/{environment_id}/risk-reviews/{review_id}/annotations",
        status_code=201,
    )
    def post_risk_annotation(
        environment_id: str,
        review_id: str,
        payload: RiskAnnotationCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="workflows:write",
            )
            review = session.scalar(
                select(RiskReview).where(
                    RiskReview.organisation_id == organisation_id,
                    RiskReview.environment_id == resolved_environment_id,
                    RiskReview.public_id == review_id,
                )
            )
            if review is None:
                raise not_found("Risk review")
            if principal.user_id is None:
                raise not_found("User")
            item = annotate_review(
                session,
                review=review,
                author_user_id=principal.user_id,
                note=payload.note,
            )
            return {"id": item.public_id, "note": item.note}

    @router.post(
        "/admin/v1/environments/{environment_id}/risk-reviews/{review_id}/escalation/disposition"
    )
    def post_risk_disposition(
        environment_id: str,
        review_id: str,
        payload: RiskDispositionCreate,
        principal: PrincipalDep,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
        ],
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> dict[str, object]:
        del idempotency_key
        require_csrf(principal, csrf_token)
        with session_factory() as session, session.begin():
            organisation_id, resolved_environment_id = resolve_admin_scope(
                session,
                principal=principal,
                environment_public_id=environment_id,
                permission="approvals:write",
            )
            review = session.scalar(
                select(RiskReview).where(
                    RiskReview.organisation_id == organisation_id,
                    RiskReview.environment_id == resolved_environment_id,
                    RiskReview.public_id == review_id,
                )
            )
            if review is None:
                raise not_found("Risk review")
            escalation = session.scalar(
                select(RiskEscalation).where(RiskEscalation.risk_review_id == review.id)
            )
            if escalation is None:
                raise not_found("Risk escalation")
            if principal.user_id is None:
                raise not_found("User")
            item = disposition_escalation(
                session,
                escalation=escalation,
                review=review,
                disposition=payload.disposition,
                note=payload.note,
                actor_user_id=principal.user_id,
                now=datetime.now(UTC),
            )
            return {"id": item.public_id, "status": item.status, "disposition": item.disposition}

    return router
