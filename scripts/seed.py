from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from relaypay.agent_runtime.models import WorkflowDefinition, WorkflowRun
from relaypay.config import Settings, get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.event_delivery.crypto import decrypt_webhook_secret, encrypt_webhook_secret
from relaypay.event_delivery.models import WebhookEndpoint, WebhookEndpointVersion
from relaypay.identity.models import (
    APIKey,
    APIKeyVersion,
    Environment,
    Organisation,
    OrganisationMembership,
    User,
)
from relaypay.identity.security import hash_password, issue_api_key
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
from relaypay.mock_bank.models import BankAccount
from relaypay.mock_commerce.models import CommerceAccount
from relaypay.mock_provider.models import ProviderAccount
from relaypay.payments.models import Customer
from relaypay.risk_review.models import RiskReview
from relaypay.risk_review.service import execute_review, prepare_review
from relaypay.risk_review.snapshot import DeterministicSiteSnapshotSource
from relaypay.settlement_intelligence.models import SettlementPolicy
from relaypay.settlement_intelligence.service import (
    ensure_daily_forecast,
)
from relaypay.settlement_intelligence.service import (
    ensure_default_policy as ensure_default_settlement_policy,
)
from relaypay.subscriptions.models import RecoveryCase, Subscription
from relaypay.subscriptions.service import (
    create_invoice,
    create_subscription,
    ensure_default_policy,
    open_recovery_case,
    record_payment_attempt,
)
from sqlalchemy import select


@dataclass(frozen=True, slots=True)
class DemoOrganisation:
    name: str
    email: str
    password: str


DEMO_ORGANISATIONS = (
    DemoOrganisation("Northstar Demo", "admin@northstar.test", "RelayPay-Northstar-2026!"),
    DemoOrganisation("Juniper Demo", "admin@juniper.test", "RelayPay-Juniper-2026!"),
)


def seed() -> list[tuple[DemoOrganisation, str]]:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="relaypay-seed"
    )
    factory = build_session_factory(engine)
    issued_keys: list[tuple[DemoOrganisation, str]] = []
    with factory() as session, session.begin():
        for demo in DEMO_ORGANISATIONS:
            existing = session.scalar(
                select(User).where(User.email_normalized == demo.email.casefold())
            )
            if existing is not None:
                continue
            organisation = Organisation(
                public_id=new_public_id("org"), name=demo.name, status="ACTIVE"
            )
            session.add(organisation)
            session.flush()
            environments = list(
                session.scalars(
                    select(Environment).where(Environment.organisation_id == organisation.id)
                )
            )
            if {item.environment_type for item in environments} != {"TEST", "LIVE_LIKE"}:
                raise RuntimeError("organisation environments were not provisioned")
            test_environment = next(
                item for item in environments if item.environment_type == "TEST"
            )
            user = User(
                email_normalized=demo.email.casefold(),
                display_name=f"{demo.name} administrator",
                password_hash=hash_password(demo.password),
                platform_role="STANDARD",
                status="ACTIVE",
            )
            session.add(user)
            session.flush()
            session.add(
                OrganisationMembership(
                    organisation_id=organisation.id,
                    user_id=user.id,
                    role="ORGANISATION_ADMIN",
                    status="ACTIVE",
                )
            )
            issued, digest = issue_api_key(
                pepper=settings.API_KEY_PEPPER.get_secret_value(), environment_type="TEST"
            )
            api_key = APIKey(
                public_id=new_public_id("key"),
                organisation_id=organisation.id,
                environment_id=test_environment.id,
                name="Seeded merchant key",
                scopes=["customers:write", "payments:read", "payments:write"],
                status="ACTIVE",
            )
            session.add(api_key)
            session.flush()
            session.add(
                APIKeyVersion(
                    organisation_id=organisation.id,
                    environment_id=test_environment.id,
                    api_key_id=api_key.id,
                    version=1,
                    public_prefix=issued.public_prefix,
                    secret_digest=digest,
                    status="ACTIVE",
                    activated_at=datetime.now(UTC),
                )
            )
            session.add_all(
                [
                    LedgerAccount(
                        organisation_id=organisation.id,
                        environment_id=test_environment.id,
                        code="PROVIDER_CLEARING_ASSET",
                        name="Provider clearing",
                        account_type="ASSET",
                        currency="INR",
                    ),
                    LedgerAccount(
                        organisation_id=organisation.id,
                        environment_id=test_environment.id,
                        code="MERCHANT_PAYABLE_LIABILITY",
                        name="Merchant payable",
                        account_type="LIABILITY",
                        currency="INR",
                    ),
                ]
            )
            issued_keys.append((demo, issued.plaintext))
        for organisation in session.scalars(select(Organisation).order_by(Organisation.id)):
            existing_test_environment = session.scalar(
                select(Environment).where(
                    Environment.organisation_id == organisation.id,
                    Environment.environment_type == "TEST",
                )
            )
            if existing_test_environment is None:
                raise RuntimeError("organisation is missing its TEST environment")
            endpoint = session.scalar(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.organisation_id == organisation.id,
                    WebhookEndpoint.environment_id == existing_test_environment.id,
                    WebhookEndpoint.name == "Bundled receiver",
                )
            )
            if endpoint is not None:
                _ensure_current_webhook_endpoint_version(
                    session,
                    endpoint=endpoint,
                    environment=existing_test_environment,
                    settings=settings,
                )
                _seed_subscription_recovery(session, organisation, existing_test_environment)
                _seed_settlement_intelligence(session, organisation, existing_test_environment)
                continue
            endpoint = WebhookEndpoint(
                public_id=new_public_id("wh"),
                organisation_id=organisation.id,
                environment_id=existing_test_environment.id,
                name="Bundled receiver",
                status="ACTIVE",
            )
            session.add(endpoint)
            session.flush()
            session.add(
                WebhookEndpointVersion(
                    public_id=new_public_id("whv"),
                    organisation_id=organisation.id,
                    environment_id=existing_test_environment.id,
                    webhook_endpoint_id=endpoint.id,
                    version=1,
                    url=f"{settings.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay",
                    encrypted_secret=encrypt_webhook_secret(
                        settings.RECEIVER_WEBHOOK_SECRET.get_secret_value(),
                        settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value(),
                    ),
                    subscribed_event_types=[
                        "payment.authorized.v1",
                        "payment.captured.v1",
                        "refund.succeeded.v1",
                    ],
                    active_from=datetime.now(UTC),
                )
            )
            _seed_subscription_recovery(session, organisation, existing_test_environment)
            _seed_settlement_intelligence(session, organisation, existing_test_environment)
    engine.dispose()
    _seed_risk_reviews()
    _seed_provider_account(settings)
    _seed_bank_account(settings)
    _seed_commerce_account(settings)
    return issued_keys


def _ensure_current_webhook_endpoint_version(
    session: object,
    *,
    endpoint: WebhookEndpoint,
    environment: Environment,
    settings: Settings,
) -> None:
    from sqlalchemy.orm import Session

    if not isinstance(session, Session):
        raise TypeError("seed requires a SQLAlchemy session")
    latest = session.scalar(
        select(WebhookEndpointVersion)
        .where(WebhookEndpointVersion.webhook_endpoint_id == endpoint.id)
        .order_by(WebhookEndpointVersion.version.desc())
        .limit(1)
    )
    expected_url = f"{settings.RECEIVER_BASE_URL.rstrip('/')}/webhooks/relaypay"
    expected_secret = settings.RECEIVER_WEBHOOK_SECRET.get_secret_value()
    encryption_key = settings.WEBHOOK_SECRET_ENCRYPTION_KEY.get_secret_value()
    current = False
    if latest is not None and latest.active_until is None:
        try:
            current = (
                decrypt_webhook_secret(latest.encrypted_secret, encryption_key) == expected_secret
            )
        except ValueError:
            current = False
    if current:
        return

    activated_at = datetime.now(UTC)
    if latest is not None and latest.active_until is None:
        latest.active_until = activated_at
    session.add(
        WebhookEndpointVersion(
            public_id=new_public_id("whv"),
            organisation_id=endpoint.organisation_id,
            environment_id=environment.id,
            webhook_endpoint_id=endpoint.id,
            version=1 if latest is None else latest.version + 1,
            url=expected_url if latest is None else latest.url,
            encrypted_secret=encrypt_webhook_secret(expected_secret, encryption_key),
            subscribed_event_types=[
                "payment.authorized.v1",
                "payment.captured.v1",
                "refund.succeeded.v1",
            ],
            active_from=activated_at,
        )
    )


def _seed_subscription_recovery(
    session: object, organisation: Organisation, environment: Environment
) -> None:
    from sqlalchemy.orm import Session

    if not isinstance(session, Session):
        raise TypeError("seed requires a SQLAlchemy session")
    existing = session.scalar(
        select(Subscription).where(
            Subscription.organisation_id == organisation.id,
            Subscription.environment_id == environment.id,
            Subscription.external_id == "synthetic-subscription-demo",
        )
    )
    if existing is not None:
        return
    definition_value = {"steps": [{"key": "recover", "kind": "SYSTEM"}]}
    definition = session.scalar(
        select(WorkflowDefinition).where(
            WorkflowDefinition.organisation_id == organisation.id,
            WorkflowDefinition.environment_id == environment.id,
            WorkflowDefinition.name == "subscription-recovery",
            WorkflowDefinition.status == "ACTIVE",
        )
    )
    if definition is None:
        definition = WorkflowDefinition(
            public_id=new_public_id("wdf"),
            organisation_id=organisation.id,
            environment_id=environment.id,
            name="subscription-recovery",
            version=1,
            definition_sha256=hashlib.sha256(
                b'{"steps":[{"key":"recover","kind":"SYSTEM"}]}'
            ).digest(),
            definition=definition_value,
            status="ACTIVE",
        )
        session.add(definition)
        session.flush([definition])
    customer = session.scalar(
        select(Customer).where(
            Customer.organisation_id == organisation.id,
            Customer.environment_id == environment.id,
            Customer.merchant_customer_reference == "subscription-recovery-demo",
        )
    )
    if customer is None:
        customer = Customer(
            public_id=new_public_id("cus"),
            organisation_id=organisation.id,
            environment_id=environment.id,
            merchant_customer_reference="subscription-recovery-demo",
            display_name="Synthetic Subscriber",
        )
        session.add(customer)
        session.flush([customer])
    subscription = create_subscription(
        session,
        organisation_id=organisation.id,
        environment_id=environment.id,
        customer_public_id=customer.public_id,
        external_id="synthetic-subscription-demo",
        plan_reference="portfolio-monthly",
        amount=12_500,
        consent={"channels": ["EMAIL", "IN_APP"], "displayName": "Synthetic Subscriber"},
    )
    session.flush([subscription])
    now = datetime.now(UTC)
    invoice = create_invoice(
        session,
        subscription=subscription,
        external_id="synthetic-invoice-demo",
        due_at=now,
    )
    session.flush([invoice])
    attempt = record_payment_attempt(
        session,
        invoice=invoice,
        provider_attempt_id=f"seed-{organisation.public_id}",
        provider_code="INSUFFICIENT_FUNDS",
        outcome="VERIFIED_FAILED",
        occurred_at=now,
        evidence={"source": "seed", "verified": True},
    )
    run = WorkflowRun(
        public_id=new_public_id("wfr"),
        organisation_id=organisation.id,
        environment_id=environment.id,
        workflow_definition_id=definition.id,
        trigger_event_id=new_public_id("bev"),
        route="EVENT:recurring-payment.failed.v1",
        idempotency_digest=hashlib.sha256(
            f"seed-subscription-recovery:{organisation.public_id}".encode()
        ).digest(),
        status="RUNNING",
        token_budget=6_000,
        cost_budget_usd_micros=60_000,
        tokens_used=0,
        cost_used_usd_micros=0,
    )
    session.add(run)
    session.flush([attempt, run])
    policy = ensure_default_policy(
        session, organisation_id=organisation.id, environment_id=environment.id
    )
    session.flush([policy])
    case = open_recovery_case(
        session,
        subscription=subscription,
        invoice=invoice,
        trigger_attempt=attempt,
        workflow_run=run,
        policy=policy,
        now=now,
    )
    if not isinstance(case, RecoveryCase):
        raise RuntimeError("subscription recovery seed did not create a case")


def _seed_provider_account(settings: Settings) -> None:
    engine = build_engine(
        settings.PROVIDER_DATABASE_URL.get_secret_value(), application_name="relaypay-provider-seed"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        existing = session.scalar(
            select(ProviderAccount).where(ProviderAccount.public_id == settings.PROVIDER_ACCOUNT_ID)
        )
        if existing is None:
            session.add(
                ProviderAccount(
                    public_id=settings.PROVIDER_ACCOUNT_ID,
                    name="RelayPay deterministic provider account",
                    signing_secret_digest=hashlib.sha256(
                        settings.PROVIDER_SIGNING_SECRET.get_secret_value().encode("utf-8")
                    ).digest(),
                )
            )
    engine.dispose()


def _seed_bank_account(settings: Settings) -> None:
    engine = build_engine(
        settings.BANK_DATABASE_URL.get_secret_value(), application_name="relaypay-bank-seed"
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        existing = session.scalar(
            select(BankAccount).where(BankAccount.public_id == settings.BANK_ACCOUNT_ID)
        )
        if existing is None:
            session.add(
                BankAccount(
                    public_id=settings.BANK_ACCOUNT_ID,
                    name="RelayPay deterministic synthetic bank account",
                    signing_secret_digest=hashlib.sha256(
                        settings.BANK_SIGNING_SECRET.get_secret_value().encode()
                    ).digest(),
                )
            )
    engine.dispose()


def _seed_commerce_account(settings: Settings) -> None:
    engine = build_engine(
        settings.COMMERCE_DATABASE_URL.get_secret_value(),
        application_name="relaypay-commerce-seed",
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        existing = session.scalar(
            select(CommerceAccount).where(CommerceAccount.public_id == settings.COMMERCE_ACCOUNT_ID)
        )
        if existing is None:
            session.add(
                CommerceAccount(
                    public_id=settings.COMMERCE_ACCOUNT_ID,
                    name="RelayPay deterministic synthetic commerce account",
                    signing_secret_digest=hashlib.sha256(
                        settings.COMMERCE_CONTROL_SECRET.get_secret_value().encode()
                    ).digest(),
                )
            )
    engine.dispose()


def _seed_settlement_intelligence(
    session: object, organisation: Organisation, environment: Environment
) -> None:
    from relaypay.merchant_balances.service import ensure_default_merchant_account
    from sqlalchemy.orm import Session

    if not isinstance(session, Session):
        raise TypeError("seed requires a SQLAlchemy session")
    existing = session.scalar(
        select(SettlementPolicy).where(
            SettlementPolicy.organisation_id == organisation.id,
            SettlementPolicy.environment_id == environment.id,
            SettlementPolicy.status == "ACTIVE",
        )
    )
    if existing is not None:
        return
    account = ensure_default_merchant_account(
        session, organisation_id=organisation.id, environment_id=environment.id
    )
    session.flush([account])
    ensure_default_settlement_policy(
        session,
        organisation_id=organisation.id,
        environment_id=environment.id,
        merchant_account_id=account.id,
    )
    ensure_daily_forecast(
        session,
        organisation_id=organisation.id,
        environment_id=environment.id,
        merchant_account_id=account.id,
        now=datetime.now(UTC),
    )


def _seed_risk_reviews() -> None:
    """Run one deterministic risk review per synthetic site archetype (first org only)."""
    from relaypay.config import get_settings
    from relaypay.database import build_engine, build_session_factory
    from relaypay.identity.models import Environment as EnvModel
    from sqlalchemy import select as _select

    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="relaypay-risk-seed"
    )
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            organisation = session.scalar(
                _select(Organisation).order_by(Organisation.created_at).limit(1)
            )
            if organisation is None:
                return
            environment = session.scalar(
                _select(EnvModel).where(
                    EnvModel.organisation_id == organisation.id,
                    EnvModel.environment_type == "TEST",
                )
            )
            if environment is None:
                return
            existing = session.scalar(
                _select(RiskReview).where(
                    RiskReview.organisation_id == organisation.id,
                    RiskReview.environment_id == environment.id,
                )
            )
            if existing is not None:
                return
        from datetime import UTC, datetime

        source = DeterministicSiteSnapshotSource()
        from relaypay.agent_runtime.contracts import ModelRequest, ModelResult, TerminalModelError
        from relaypay.idempotency import canonical_json_bytes as _cj
        from relaypay.risk_review.findings import (
            ModelFindings,
            deterministic_findings,
        )

        class _SeedFindingsProvider:
            name = "fake"

            def generate_structured(self, request: ModelRequest) -> ModelResult:
                if request.schema is not ModelFindings:
                    raise TerminalModelError("unsupported schema")
                import json as _json

                marker = "<relaypay-untrusted-evidence>\n"
                start = request.prompt.index(marker) + len(marker)
                end = request.prompt.index("\n</relaypay-untrusted-evidence>", start)
                snapshot = _json.loads(request.prompt[start:end])
                output = deterministic_findings(snapshot)
                response_bytes = _cj(output.model_dump(mode="json"))
                return ModelResult(
                    output=output,
                    provider=self.name,
                    model_id=request.model_id,
                    request_bytes=_cj({"model": request.model_id, "prompt": request.prompt}),
                    response_bytes=response_bytes,
                    latency_ms=0,
                    input_tokens=max(1, len(request.prompt) // 4),
                    output_tokens=max(1, len(response_bytes) // 4),
                    finish_status="STOP",
                )

        provider = _SeedFindingsProvider()
        for site_ref in ("COMPLETE_CLEAN", "SUSPICIOUS_CLAIMS", "PROHIBITED_CATEGORY"):
            prepared = prepare_review(
                factory,
                organisation_id=organisation.id,
                environment_id=environment.id,
                organisation_public_id=organisation.public_id,
                environment_public_id=environment.public_id,
                site_ref=site_ref,
                source=source,
                source_url=settings.RISK_SITE_BASE_URL,
            )
            if not prepared.replayed:
                execute_review(
                    factory,
                    prepared,
                    provider=provider,
                    now=datetime.now(UTC),
                )
    finally:
        engine.dispose()


def main() -> None:
    issued = seed()
    if not issued:
        print("Demo organisations already exist; no API key material was reissued.")
        return
    print("Synthetic demo credentials (API keys are shown once):")
    for demo, api_key in issued:
        print(f"- {demo.name}: {demo.email} / {demo.password}")
        print(f"  API key: {api_key}")


if __name__ == "__main__":
    main()
