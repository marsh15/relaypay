"""Isolated-tenant builder plus the common money-path drivers for proof tests.

``build_world`` creates one organisation with its TEST environment, default merchant
account (and its ledger accounts), clearing ledger account, a customer, an API-key
principal and an organisation-admin session principal. Every identifier carries a fresh
uuid suffix, so any number of worlds can coexist in one database across parallel tests and
chaos seeds. The drivers call the real service functions; only the provider is faked.
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache

from pydantic import BaseModel
from relaypay.config import get_settings
from relaypay.contracts import CustomerCreate, EmptyCommand, PaymentIntentCreate, RefundCreate
from relaypay.idempotency import Fingerprint, build_fingerprint
from relaypay.identity.models import (
    APIKey,
    APIKeyVersion,
    Environment,
    Organisation,
    OrganisationMembership,
    User,
)
from relaypay.identity.security import Principal, hash_password, issue_api_key
from relaypay.ids import new_public_id
from relaypay.ledger.models import LedgerAccount
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.merchant_balances.service import (
    MerchantBalances,
    derive_balances,
    ensure_default_merchant_account,
    run_settlement,
)
from relaypay.payments.models import PaymentIntent
from relaypay.payments.service import (
    create_customer,
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
    initiate_refund,
)
from relaypay.payouts.service import CommandResult, create_beneficiary, create_payout
from relaypay.provider_operations.models import ProviderOperation
from relaypay.provider_operations.recovery import claim_specific_operation, recover_claim
from relaypay.provider_operations.service import ProviderTransport, dispatch_operation
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.fake_provider import ACCOUNT_ID, SIGNING_SECRET

KEY_PEPPER = "proof-world-idempotency-pepper"
_FALLBACK_API_KEY_PEPPER = "dev-api-key-pepper-change-me-32-bytes"  # matches .env.example
_ADMIN_PASSWORD = "Synthetic-World-Admin-Password!"


@cache
def _admin_password_hash() -> str:
    # Argon2 is deliberately slow; hash once per process, not once per world.
    return hash_password(_ADMIN_PASSWORD)


def _api_key_pepper() -> str:
    """Pepper a locally running API server would use, so ``World.api_key`` works over HTTP."""
    if "API_KEY_PEPPER" in os.environ:
        return os.environ["API_KEY_PEPPER"]
    try:
        return get_settings().API_KEY_PEPPER.get_secret_value()
    except Exception:  # settings need a full env; fall back to .env.example
        return _FALLBACK_API_KEY_PEPPER


@dataclass(frozen=True, slots=True)
class World:
    """Everything needed to call payment, settlement and payout services for one tenant."""

    organisation_id: uuid.UUID
    organisation_public_id: str
    environment_id: uuid.UUID
    environment_public_id: str
    merchant_account_id: uuid.UUID
    merchant_public_id: str
    customer_public_id: str
    api_principal: Principal  # kind=API_KEY, environment-scoped: payments commands
    admin_principal: Principal  # kind=SESSION, ORGANISATION_ADMIN: settlement and payouts
    api_key: str = field(repr=False)  # plaintext key for HTTP tests (needs the API's pepper)
    key_pepper: str = KEY_PEPPER  # idempotency-key pepper for direct service calls
    provider_account_id: str = ACCOUNT_ID
    provider_signing_secret: str = SIGNING_SECRET


@dataclass(frozen=True, slots=True)
class PaidPayment:
    payment_id: str
    amount: int
    authorization_operation_id: str
    capture_operation_id: str


class PayoutBody(BaseModel):
    merchant_account_id: str
    beneficiary_id: str
    amount: int


def build_world(factory: sessionmaker[Session], *, label: str = "world") -> World:
    """Create one fully isolated tenant (organisation, TEST env, merchant, customer, keys)."""
    tag = uuid.uuid4().hex
    api_pepper = _api_key_pepper()
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name=f"{label} {tag}", status="ACTIVE"
        )
        user = User(
            email_normalized=f"{label}-{tag}@example.test",
            display_name=f"{label} admin",
            password_hash=_admin_password_hash(),
            status="ACTIVE",
        )
        session.add_all([organisation, user])
        session.flush()
        session.add(
            OrganisationMembership(
                organisation_id=organisation.id,
                user_id=user.id,
                role="ORGANISATION_ADMIN",
                status="ACTIVE",
            )
        )
        environment = session.scalar(
            select(Environment).where(
                Environment.organisation_id == organisation.id,
                Environment.environment_type == "TEST",
            )
        )
        if environment is None:
            raise RuntimeError("organisation was created without a TEST environment")
        session.add(
            LedgerAccount(
                organisation_id=organisation.id,
                environment_id=environment.id,
                code="PROVIDER_CLEARING_ASSET",
                name="Provider clearing",
                account_type="ASSET",
                currency="INR",
            )
        )
        merchant = ensure_default_merchant_account(
            session, organisation_id=organisation.id, environment_id=environment.id
        )
        scopes = ["customers:write", "payments:read", "payments:write"]
        issued, digest = issue_api_key(pepper=api_pepper, environment_type="TEST")
        api_key = APIKey(
            public_id=new_public_id("key"),
            organisation_id=organisation.id,
            environment_id=environment.id,
            name=f"{label} key {tag}",
            scopes=scopes,
            status="ACTIVE",
        )
        session.add(api_key)
        session.flush()
        session.add(
            APIKeyVersion(
                organisation_id=organisation.id,
                environment_id=environment.id,
                api_key_id=api_key.id,
                version=1,
                public_prefix=issued.public_prefix,
                secret_digest=digest,
                status="ACTIVE",
                activated_at=datetime.now(UTC),
            )
        )
        session.flush()
        api_principal = Principal(
            kind="API_KEY",
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=environment.id,
            environment_public_id=environment.public_id,
            display_name=api_key.name,
            scopes=frozenset(scopes),
            api_key_id=api_key.id,
        )
        admin_principal = Principal(
            kind="SESSION",
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=None,
            environment_public_id=None,
            display_name=user.display_name,
            scopes=frozenset(),
            membership_role="ORGANISATION_ADMIN",
            user_id=user.id,
        )
        organisation_id, organisation_public_id = organisation.id, organisation.public_id
        environment_id, environment_public_id = environment.id, environment.public_id
        merchant_id, merchant_public_id = merchant.id, merchant.public_id
    customer = create_customer(
        factory,
        organisation_id=organisation_id,
        environment_id=environment_id,
        payload=CustomerCreate(
            merchant_customer_reference=f"customer-{tag}", display_name="Synthetic customer"
        ),
    )
    return World(
        organisation_id=organisation_id,
        organisation_public_id=organisation_public_id,
        environment_id=environment_id,
        environment_public_id=environment_public_id,
        merchant_account_id=merchant_id,
        merchant_public_id=merchant_public_id,
        customer_public_id=customer.public_id,
        api_principal=api_principal,
        admin_principal=admin_principal,
        api_key=issued.plaintext,
    )


# ----- payments ----------------------------------------------------------------------------


def _command_fingerprint(
    payment_id: str, suffix: str, body: EmptyCommand | RefundCreate
) -> Fingerprint:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template=f"/payment_intents/{{payment_intent_id}}/{suffix}",
        path_params={"payment_intent_id": payment_id},
        body=body,
    )


def create_payment(factory: sessionmaker[Session], world: World, *, amount: int = 100_000) -> str:
    """Create a payment intent (real service); returns its public id."""
    payload = PaymentIntentCreate(
        customer_id=world.customer_public_id,
        merchant_reference=f"order-{uuid.uuid4().hex}",
        amount=amount,
        currency="INR",
    )
    result = create_payment_intent(
        factory,
        organisation_id=world.organisation_id,
        environment_id=world.environment_id,
        payload=payload,
        idempotency_key=f"payment-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="v1",
            method="POST",
            route_template="/payment_intents",
            path_params={},
            body=payload,
        ),
        key_pepper=world.key_pepper,
    )
    return str(json.loads(result.body)["id"])


def dispatch(
    factory: sessionmaker[Session],
    world: World,
    operation_id: str,
    provider: ProviderTransport,
) -> None:
    """Send an initiated operation through ``provider`` (first send, or lookup if sent)."""
    dispatch_operation(
        factory,
        organisation_id=world.organisation_id,
        environment_id=world.environment_id,
        operation_public_id=operation_id,
        provider_account_id=world.provider_account_id,
        provider_signing_secret=world.provider_signing_secret,
        transport=provider,
    )


def authorize(
    factory: sessionmaker[Session],
    world: World,
    payment_id: str,
    provider: ProviderTransport | None = None,
    *,
    key: str | None = None,
) -> str:
    """Initiate an authorization (dispatching it when ``provider`` is given); returns op id."""
    result = initiate_authorization(
        factory,
        organisation_id=world.organisation_id,
        environment_id=world.environment_id,
        payment_public_id=payment_id,
        idempotency_key=key or f"authorize-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_id, "authorize", EmptyCommand()),
        key_pepper=world.key_pepper,
    )
    operation_id = str(json.loads(result.body)["operationId"])
    if provider is not None:
        dispatch(factory, world, operation_id, provider)
    return operation_id


def capture(
    factory: sessionmaker[Session],
    world: World,
    payment_id: str,
    provider: ProviderTransport | None = None,
    *,
    key: str | None = None,
) -> str:
    """Initiate a capture (dispatching it when ``provider`` is given); returns op id."""
    result = initiate_capture(
        factory,
        organisation_id=world.organisation_id,
        environment_id=world.environment_id,
        payment_public_id=payment_id,
        idempotency_key=key or f"capture-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_id, "capture", EmptyCommand()),
        key_pepper=world.key_pepper,
    )
    operation_id = str(json.loads(result.body)["operationId"])
    if provider is not None:
        dispatch(factory, world, operation_id, provider)
    return operation_id


def refund(
    factory: sessionmaker[Session],
    world: World,
    payment_id: str,
    amount: int,
    provider: ProviderTransport | None = None,
    *,
    key: str | None = None,
) -> str:
    """Initiate a refund (dispatching it when ``provider`` is given); returns op id."""
    payload = RefundCreate(
        amount=amount, currency="INR", merchant_refund_reference=f"refund-{uuid.uuid4().hex}"
    )
    result = initiate_refund(
        factory,
        organisation_id=world.organisation_id,
        environment_id=world.environment_id,
        payment_public_id=payment_id,
        payload=payload,
        idempotency_key=key or f"refund-{uuid.uuid4().hex}",
        fingerprint=_command_fingerprint(payment_id, "refunds", payload),
        key_pepper=world.key_pepper,
    )
    operation_id = str(json.loads(result.body)["operationId"])
    if provider is not None:
        dispatch(factory, world, operation_id, provider)
    return operation_id


def recover(
    factory: sessionmaker[Session],
    world: World,
    operation_id: str,
    provider: ProviderTransport,
) -> None:
    """Run one provider status lookup for a non-terminal operation (ignores backoff timing)."""
    claim = claim_specific_operation(
        factory, organisation_id=world.organisation_id, operation_public_id=operation_id
    )
    recover_claim(
        factory,
        claim=claim,
        provider_account_id=world.provider_account_id,
        provider_signing_secret=world.provider_signing_secret,
        transport=provider,
        actor_type="ADMIN_LOOKUP",
    )


def operation_status(factory: sessionmaker[Session], world: World, operation_id: str) -> str:
    with factory() as session, session.begin():
        status = session.scalar(
            select(ProviderOperation.status).where(
                ProviderOperation.organisation_id == world.organisation_id,
                ProviderOperation.public_id == operation_id,
            )
        )
    if status is None:
        raise LookupError(f"provider operation {operation_id} not found")
    return str(status)


def paid_payment(
    factory: sessionmaker[Session],
    world: World,
    provider: ProviderTransport,
    *,
    amount: int = 100_000,
) -> PaidPayment:
    """Create -> authorize -> capture, each driven synchronously through ``provider``.

    With a default-SUCCEED provider the capture is SUCCEEDED (journal posted) on return.
    """
    payment_id = create_payment(factory, world, amount=amount)
    authorization_op = authorize(factory, world, payment_id, provider)
    capture_op = capture(factory, world, payment_id, provider)
    return PaidPayment(payment_id, amount, authorization_op, capture_op)


# ----- balances, settlement and payouts ----------------------------------------------------


def balances(factory: sessionmaker[Session], world: World) -> MerchantBalances:
    """Posting-derived pending/available/receivable/reserved balances of the default merchant."""
    with factory() as session, session.begin():
        merchant = session.get(MerchantAccount, world.merchant_account_id)
        if merchant is None:
            raise LookupError("default merchant account is missing")
        return derive_balances(session, merchant)


def settle(factory: sessionmaker[Session], world: World) -> int:
    """Run settlement for the default merchant; returns the settled amount in paise."""
    result = run_settlement(
        factory,
        principal=world.admin_principal,
        environment_public_id=world.environment_public_id,
        merchant_public_id=world.merchant_public_id,
        idempotency_key=f"settlement-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template=(
                "/environments/{environment_id}/merchant-accounts/{merchant_account_id}/settlements"
            ),
            path_params={
                "environment_id": world.environment_public_id,
                "merchant_account_id": world.merchant_public_id,
            },
            body=EmptyCommand(),
        ),
        key_pepper=world.key_pepper,
    )
    return int(json.loads(result.body)["settledAmount"])


def fund_available(
    factory: sessionmaker[Session],
    world: World,
    provider: ProviderTransport,
    amount: int,
) -> PaidPayment:
    """Capture a payment of ``amount`` and settle it, so AVAILABLE can fund payouts.

    Requires a provider that succeeds. Settlement sweeps all pending, so earlier captures in
    the same world are settled too.
    """
    flow = paid_payment(factory, world, provider, amount=amount)
    settle(factory, world)
    return flow


def make_beneficiary(factory: sessionmaker[Session], world: World) -> str:
    """Create a beneficiary (real service); returns its public id."""
    with factory() as session, session.begin():
        beneficiary = create_beneficiary(
            session,
            principal=world.admin_principal,
            environment_public_id=world.environment_public_id,
            reference=f"beneficiary-{uuid.uuid4().hex}",
            display_name="Synthetic beneficiary",
            bank_account_reference=f"synthetic-bank-{uuid.uuid4().hex}",
        )
        return beneficiary.public_id


def request_payout(
    factory: sessionmaker[Session],
    world: World,
    beneficiary_id: str,
    amount: int,
    *,
    key: str | None = None,
) -> CommandResult:
    """Reserve a payout via the real ``create_payout`` (raises RelayPayError if insufficient)."""
    body = PayoutBody(
        merchant_account_id=world.merchant_public_id,
        beneficiary_id=beneficiary_id,
        amount=amount,
    )
    return create_payout(
        factory,
        principal=world.admin_principal,
        environment_public_id=world.environment_public_id,
        merchant_public_id=world.merchant_public_id,
        beneficiary_public_id=beneficiary_id,
        amount=amount,
        idempotency_key=key or f"payout-{uuid.uuid4().hex}",
        fingerprint=build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template="/environments/{environment_id}/payouts",
            path_params={"environment_id": world.environment_public_id},
            body=body,
        ),
        key_pepper=world.key_pepper,
    )


def payment_row(factory: sessionmaker[Session], world: World, payment_id: str) -> PaymentIntent:
    """Fetch the payment intent row (detached) for assertions."""
    with factory() as session, session.begin():
        payment = session.scalar(
            select(PaymentIntent).where(
                PaymentIntent.organisation_id == world.organisation_id,
                PaymentIntent.public_id == payment_id,
            )
        )
        if payment is None:
            raise LookupError(f"payment {payment_id} not found")
        return payment
