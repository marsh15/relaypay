import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError, not_found
from relaypay.observability.metrics import operations_metrics
from relaypay.provider_operations.finalizer import ActorType
from relaypay.provider_operations.models import ProviderOperation
from relaypay.provider_operations.service import (
    DEFAULT_NOT_FOUND_GRACE_SECONDS,
    ProviderTransport,
    apply_recorded_outcome,
    classify_and_record_lookup,
    dispatch_operation,
)


@dataclass(frozen=True, slots=True)
class RecoveryClaim:
    operation_id: uuid.UUID
    organisation_id: uuid.UUID
    operation_public_id: str
    stable_key: str
    lease_token: uuid.UUID


def claim_due_operations(
    factory: sessionmaker[Session],
    *,
    batch_size: int = 20,
    lease_seconds: int = 30,
    organisation_id: uuid.UUID | None = None,
) -> list[RecoveryClaim]:
    now = datetime.now(UTC)
    claims: list[RecoveryClaim] = []
    reclaimed = 0
    with factory() as session, session.begin():
        statement = (
            select(ProviderOperation)
            .where(
                ProviderOperation.status == "PROCESSING",
                ProviderOperation.last_sent_at.is_not(None),
                ProviderOperation.next_lookup_at <= now,
                or_(
                    ProviderOperation.lookup_lease_expires_at.is_(None),
                    ProviderOperation.lookup_lease_expires_at <= now,
                ),
            )
            .order_by(ProviderOperation.next_lookup_at, ProviderOperation.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        if organisation_id is not None:
            statement = statement.where(ProviderOperation.organisation_id == organisation_id)
        operations = list(session.scalars(statement))
        for operation in operations:
            if (
                operation.lookup_lease_expires_at is not None
                and operation.lookup_lease_expires_at <= now
            ):
                reclaimed += 1
            token = uuid.uuid4()
            operation.lookup_lease_token = token
            operation.lookup_lease_expires_at = now + timedelta(seconds=lease_seconds)
            claims.append(
                RecoveryClaim(
                    operation.id,
                    operation.organisation_id,
                    operation.public_id,
                    operation.stable_provider_key,
                    token,
                )
            )
    if reclaimed:
        operations_metrics().recoveries.labels("expired_lease_reclaimed").inc(reclaimed)
    return claims


# An operation the API committed but never sent (the process died between commit and dispatch)
# has no ``last_sent_at``, so ``claim_due_operations`` never sees it. Inline dispatch follows the
# commit within milliseconds, so one still unsent after the grace period has lost its dispatcher.
UNSENT_DISPATCH_GRACE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class UnsentClaim:
    organisation_id: uuid.UUID
    environment_id: uuid.UUID
    operation_public_id: str


def claim_unsent_operations(
    factory: sessionmaker[Session],
    *,
    grace_seconds: int = UNSENT_DISPATCH_GRACE_SECONDS,
    batch_size: int = 20,
    lease_seconds: int = 30,
    organisation_id: uuid.UUID | None = None,
) -> list[UnsentClaim]:
    """Lease PROCESSING operations still unsent ``grace_seconds`` after creation.

    The lease only stops two pollers dispatching the same operation. Exactly-once sending comes
    from ``prepare_first_send``, which re-locks the row and turns any caller that finds
    ``last_sent_at`` already set (the API's own late dispatch, or another poller) into a lookup.
    """
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        statement = (
            select(ProviderOperation)
            .where(
                ProviderOperation.status == "PROCESSING",
                ProviderOperation.last_sent_at.is_(None),
                ProviderOperation.created_at <= now - timedelta(seconds=grace_seconds),
                or_(
                    ProviderOperation.lookup_lease_expires_at.is_(None),
                    ProviderOperation.lookup_lease_expires_at <= now,
                ),
            )
            .order_by(ProviderOperation.created_at, ProviderOperation.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        if organisation_id is not None:
            statement = statement.where(ProviderOperation.organisation_id == organisation_id)
        claims: list[UnsentClaim] = []
        for operation in session.scalars(statement):
            operation.lookup_lease_token = uuid.uuid4()
            operation.lookup_lease_expires_at = now + timedelta(seconds=lease_seconds)
            claims.append(
                UnsentClaim(
                    operation.organisation_id, operation.environment_id, operation.public_id
                )
            )
    return claims


def dispatch_unsent_operations(
    factory: sessionmaker[Session],
    *,
    provider_account_id: str,
    provider_signing_secret: str,
    transport: ProviderTransport,
    grace_seconds: int = UNSENT_DISPATCH_GRACE_SECONDS,
    batch_size: int = 20,
    organisation_id: uuid.UUID | None = None,
) -> int:
    """First-send operations whose dispatcher died after the API committed them."""
    claims = claim_unsent_operations(
        factory,
        grace_seconds=grace_seconds,
        batch_size=batch_size,
        organisation_id=organisation_id,
    )
    for claim in claims:
        dispatch_operation(
            factory,
            organisation_id=claim.organisation_id,
            environment_id=claim.environment_id,
            operation_public_id=claim.operation_public_id,
            provider_account_id=provider_account_id,
            transport=transport,
            provider_signing_secret=provider_signing_secret,
        )
    if claims:
        operations_metrics().recoveries.labels("unsent_dispatch").inc(len(claims))
    return len(claims)


def claim_specific_operation(
    factory: sessionmaker[Session],
    *,
    organisation_id: uuid.UUID,
    operation_public_id: str,
    lease_seconds: int = 30,
    require_review: bool = False,
) -> RecoveryClaim:
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        operation = session.scalar(
            select(ProviderOperation)
            .where(
                ProviderOperation.organisation_id == organisation_id,
                ProviderOperation.public_id == operation_public_id,
            )
            .with_for_update()
        )
        if operation is None:
            raise not_found("Provider operation")
        if operation.last_sent_at is None:
            raise RelayPayError(
                code="LOOKUP_NOT_AVAILABLE",
                message="Provider status lookup is unavailable before the first recorded send",
                http_status=409,
            )
        if require_review and operation.status != "REQUIRES_REVIEW":
            raise RelayPayError(
                code="OPERATION_NOT_IN_REVIEW",
                message="Administrative lookup is available only for review operations",
                http_status=409,
            )
        if operation.status in {"SUCCEEDED", "FAILED"}:
            raise RelayPayError(
                code="OPERATION_TERMINAL",
                message="A terminal provider operation does not require lookup",
                http_status=409,
            )
        if (
            operation.lookup_lease_expires_at is not None
            and operation.lookup_lease_expires_at > now
        ):
            raise RelayPayError(
                code="LOOKUP_IN_PROGRESS",
                message="A provider status lookup is already in progress",
                http_status=409,
            )
        token = uuid.uuid4()
        operation.lookup_lease_token = token
        operation.lookup_lease_expires_at = now + timedelta(seconds=lease_seconds)
        return RecoveryClaim(
            operation.id,
            operation.organisation_id,
            operation.public_id,
            operation.stable_provider_key,
            token,
        )


def recover_claim(
    factory: sessionmaker[Session],
    *,
    claim: RecoveryClaim,
    provider_account_id: str,
    provider_signing_secret: str,
    transport: ProviderTransport,
    actor_type: ActorType = "RECOVERY_WORKER",
    not_found_grace_seconds: int = DEFAULT_NOT_FOUND_GRACE_SECONDS,
) -> None:
    try:
        observation = transport.lookup(
            account_id=provider_account_id,
            stable_key=claim.stable_key,
        )
    except Exception:
        observation = None
    outcome = classify_and_record_lookup(
        factory,
        operation_id=claim.operation_id,
        lease_token=claim.lease_token,
        provider_account_id=provider_account_id,
        provider_signing_secret=provider_signing_secret,
        observation=observation,
        not_found_grace_seconds=not_found_grace_seconds,
    )
    apply_recorded_outcome(
        factory,
        operation_id=claim.operation_id,
        outcome=outcome,
        actor_type=actor_type,
        correlation_id=f"lookup:{claim.lease_token}",
    )


def run_recovery_batch(
    factory: sessionmaker[Session],
    *,
    provider_account_id: str,
    provider_signing_secret: str,
    transport: ProviderTransport,
    batch_size: int = 20,
    not_found_grace_seconds: int = DEFAULT_NOT_FOUND_GRACE_SECONDS,
    organisation_id: uuid.UUID | None = None,
) -> int:
    dispatched = dispatch_unsent_operations(
        factory,
        provider_account_id=provider_account_id,
        provider_signing_secret=provider_signing_secret,
        transport=transport,
        batch_size=batch_size,
        organisation_id=organisation_id,
    )
    claims = claim_due_operations(factory, batch_size=batch_size, organisation_id=organisation_id)
    for claim in claims:
        recover_claim(
            factory,
            claim=claim,
            provider_account_id=provider_account_id,
            provider_signing_secret=provider_signing_secret,
            transport=transport,
            not_found_grace_seconds=not_found_grace_seconds,
        )
    if claims:
        operations_metrics().recoveries.labels("provider_lookup").inc(len(claims))
    return dispatched + len(claims)
