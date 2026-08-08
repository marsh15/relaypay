import hashlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.agent_runtime.models import WorkflowDefinition, WorkflowRun
from relaypay.agent_runtime.workflows import decide_approval
from relaypay.database import build_engine, build_session_factory
from relaypay.disputes.models import DisputeCase, DisputePackageVersion
from relaypay.disputes.network import DeterministicDisputeNetwork
from relaypay.disputes.package import PackageFile
from relaypay.disputes.service import (
    Citation,
    StructuredDisputeDraft,
    create_draft,
    freeze_draft,
    mark_package_approved,
    open_case,
    submit_approved_package,
)
from relaypay.errors import RelayPayError
from relaypay.identity.models import Environment, Organisation
from relaypay.identity.security import Principal
from relaypay.ids import new_public_id, new_uuid
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.payments.models import Customer, PaymentIntent
from sqlalchemy import select

pytestmark = pytest.mark.integration


def test_dispute_edit_invalidates_approval_and_ambiguous_submit_has_one_effect() -> None:
    database_url = os.getenv(
        "RELAYPAY_DATABASE_URL",
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
    )
    engine = build_engine(database_url, application_name="m11-dispute-proof")
    factory = build_session_factory(engine)
    organisation_id = new_uuid()
    try:
        with factory() as session, session.begin():
            organisation = Organisation(
                id=organisation_id,
                public_id=new_public_id("org"),
                name="Dispute proof",
                status="ACTIVE",
            )
            session.add(organisation)
            session.flush()
            environment = (
                session.query(Environment)
                .filter_by(
                    organisation_id=organisation_id,
                    environment_type="TEST",
                )
                .one()
            )
            environment_id = environment.id
            customer = Customer(
                id=new_uuid(),
                public_id=new_public_id("cus"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                merchant_customer_reference=f"customer-{new_uuid().hex}",
                display_name="Synthetic Customer",
            )
            merchant = (
                session.query(MerchantAccount)
                .filter_by(
                    organisation_id=organisation_id,
                    environment_id=environment_id,
                    is_default=True,
                )
                .one()
            )
            payment = PaymentIntent(
                id=new_uuid(),
                public_id=new_public_id("pay"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                customer_id=customer.id,
                merchant_account_id=merchant.id,
                merchant_reference=f"payment-{new_uuid().hex}",
                amount=12_500,
                currency="INR",
            )
            definition = WorkflowDefinition(
                id=new_uuid(),
                public_id=new_public_id("wdf"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                name="dispute-response",
                version=1,
                definition_sha256=hashlib.sha256(b"dispute-response-v1").digest(),
                definition={"steps": []},
                status="ACTIVE",
            )
            run = WorkflowRun(
                id=new_uuid(),
                public_id=new_public_id("wfr"),
                organisation_id=organisation_id,
                environment_id=environment_id,
                workflow_definition_id=definition.id,
                route="EVENT:dispute.created.v1",
                idempotency_digest=hashlib.sha256(new_uuid().bytes).digest(),
                status="RUNNING",
                token_budget=10_000,
                cost_budget_usd_micros=100_000,
                tokens_used=0,
                cost_used_usd_micros=0,
            )
            session.add_all([customer, payment, definition, run])
            case = open_case(
                session,
                organisation_id=organisation_id,
                environment_id=environment_id,
                payment_public_id=payment.public_id,
                workflow_run_id=run.id,
                network_dispute_id=f"network-{new_uuid().hex}",
                reason_code="PRODUCT_NOT_RECEIVED",
                amount=payment.amount,
                due_at=datetime.now(UTC) + timedelta(days=7),
                source_snapshot={"paymentId": payment.public_id, "deliveryStatus": "DELIVERED"},
            )
            session.flush()
            citation = Citation.model_validate(
                {
                    "recordType": "dispute_case_snapshot",
                    "recordId": case.public_id,
                    "fieldPaths": ["paymentId", "deliveryStatus"],
                    "snapshotSha256": case.source_sha256.hex(),
                }
            )
            fabricated = Citation.model_validate(
                {
                    "recordType": "dispute_case_snapshot",
                    "recordId": case.public_id,
                    "fieldPaths": ["fabricatedField"],
                    "snapshotSha256": case.source_sha256.hex(),
                }
            )
            with pytest.raises(RelayPayError) as invalid_citation:
                create_draft(
                    session,
                    case=case,
                    draft=StructuredDisputeDraft(
                        classification="CONTEST",
                        confidence="HIGH",
                        missingEvidence=[],
                        responseText="A fabricated citation must be rejected.",
                        citations=[fabricated],
                    ),
                    author_type="AGENT",
                    author_user_id=None,
                )
            assert invalid_citation.value.code == "INVALID_CITATION"
            draft = create_draft(
                session,
                case=case,
                draft=StructuredDisputeDraft(
                    classification="CONTEST",
                    confidence="HIGH",
                    missingEvidence=[],
                    responseText="The synthetic delivery evidence confirms fulfilment.",
                    citations=[citation],
                ),
                author_type="AGENT",
                author_user_id=None,
            )
            session.flush()
            maker_user_id = new_uuid()
            invalidated_package, invalidated_approval = freeze_draft(
                session,
                case=case,
                draft=draft,
                attachments=(PackageFile("delivery.txt", "text/plain", b"delivered"),),
                signing_secret=b"synthetic-dispute-signing-secret",
                maker_user_id=maker_user_id,
            )
            session.flush()
            revised_draft = create_draft(
                session,
                case=case,
                draft=StructuredDisputeDraft(
                    classification="CONTEST",
                    confidence="MEDIUM",
                    missingEvidence=["deliveryProof"],
                    responseText="An analyst revised the response without mutating prior evidence.",
                    citations=[citation],
                ),
                author_type="ANALYST",
                author_user_id=maker_user_id,
            )
            session.flush()
            assert invalidated_package.status == "INVALIDATED"
            assert invalidated_approval.status == "INVALIDATED"
            package, approval = freeze_draft(
                session,
                case=case,
                draft=revised_draft,
                attachments=(PackageFile("delivery.txt", "text/plain", b"delivered"),),
                signing_secret=b"synthetic-dispute-signing-secret",
                maker_user_id=maker_user_id,
            )
            session.flush()
            frozen_bytes = package.package_bytes
            self_approver = Principal(
                kind="SESSION",
                organisation_id=organisation_id,
                organisation_public_id=organisation.public_id,
                environment_id=None,
                environment_public_id=None,
                display_name="Maker",
                scopes=frozenset({"approvals:write"}),
                membership_role="APPROVER",
                user_id=maker_user_id,
            )
            with pytest.raises(RelayPayError) as self_approval:
                decide_approval(
                    session,
                    principal=self_approver,
                    environment_public_id=environment.public_id,
                    request_public_id=approval.public_id,
                    decision="APPROVED",
                    note=None,
                )
            assert self_approval.value.code == "MAKER_CHECKER_REQUIRED"
            approver = Principal(
                kind="SESSION",
                organisation_id=organisation_id,
                organisation_public_id=organisation.public_id,
                environment_id=None,
                environment_public_id=None,
                display_name="Independent approver",
                scopes=frozenset({"approvals:write"}),
                membership_role="APPROVER",
                user_id=new_uuid(),
            )
            decide_approval(
                session,
                principal=approver,
                environment_public_id=environment.public_id,
                request_public_id=approval.public_id,
                decision="APPROVED",
                note="Exact package reviewed",
            )
            mark_package_approved(session, package_public_id=package.public_id, approval=approval)
            package_public_id = package.public_id

        network = DeterministicDisputeNetwork(lose_first_response=True)
        first = submit_approved_package(
            factory, package_public_id=package_public_id, network=network
        )
        assert first.status == "AMBIGUOUS"
        second = submit_approved_package(
            factory, package_public_id=package_public_id, network=network
        )
        assert second.status == "SUCCEEDED"
        assert network.effect_count == 1
        with factory() as session, session.begin():
            persisted = session.scalar(
                select(DisputePackageVersion).where(
                    DisputePackageVersion.public_id == package_public_id
                )
            )
            case = session.scalar(
                select(DisputeCase).where(DisputeCase.organisation_id == organisation_id)
            )
            assert persisted is not None and persisted.package_bytes == frozen_bytes
            assert persisted.status == "SUBMITTED"
            assert case is not None and case.status == "SUBMITTED"
    finally:
        engine.dispose()
