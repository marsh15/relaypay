import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError, not_found
from relaypay.identity.models import (
    APIKey,
    APIKeyVersion,
    AuditRecord,
    Environment,
    Organisation,
    OrganisationMembership,
)
from relaypay.identity.security import IssuedAPIKey, Principal, issue_api_key
from relaypay.ids import new_public_id


def require_organisation_admin(principal: Principal) -> None:
    if principal.kind != "SESSION" or principal.membership_role != "ORGANISATION_ADMIN":
        raise RelayPayError(
            code="FORBIDDEN",
            message="Organisation administrator permission required",
            http_status=403,
        )


def provision_organisation(session: Session, *, principal: Principal, name: str) -> Organisation:
    if principal.kind != "SESSION" or principal.platform_role != "PLATFORM_ADMIN":
        raise RelayPayError(
            code="FORBIDDEN", message="Platform administrator permission required", http_status=403
        )
    organisation = Organisation(public_id=new_public_id("org"), name=name, status="ACTIVE")
    session.add(organisation)
    session.flush()
    if principal.user_id is not None:
        session.add(
            OrganisationMembership(
                organisation_id=organisation.id,
                user_id=principal.user_id,
                role="ORGANISATION_ADMIN",
                status="ACTIVE",
            )
        )
    append_audit(
        session,
        principal=principal,
        action="ORGANISATION_PROVISIONED",
        target_type="ORGANISATION",
        target_id=organisation.public_id,
    )
    return organisation


def append_audit(
    session: Session,
    *,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: str,
    environment_id: uuid.UUID | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditRecord(
            public_id=new_public_id("aud"),
            organisation_id=principal.organisation_id,
            environment_id=environment_id,
            actor_type="USER",
            actor_id=principal.user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
    )


def list_environments(session: Session, principal: Principal) -> list[Environment]:
    return list(
        session.scalars(
            select(Environment)
            .where(Environment.organisation_id == principal.organisation_id)
            .order_by(Environment.environment_type.desc())
        )
    )


def _environment(session: Session, principal: Principal, public_id: str) -> Environment:
    environment = session.scalar(
        select(Environment).where(
            Environment.organisation_id == principal.organisation_id,
            Environment.public_id == public_id,
            Environment.status == "ACTIVE",
        )
    )
    if environment is None:
        raise not_found("Environment")
    return environment


def create_api_key(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    name: str,
    scopes: list[str],
    pepper: str,
) -> tuple[APIKey, APIKeyVersion, IssuedAPIKey]:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    issued, digest = issue_api_key(
        pepper=pepper,
        environment_type=environment.environment_type,  # type: ignore[arg-type]
    )
    key = APIKey(
        public_id=new_public_id("key"),
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        name=name,
        scopes=scopes,
        status="ACTIVE",
    )
    session.add(key)
    session.flush()
    version = APIKeyVersion(
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        api_key_id=key.id,
        version=1,
        public_prefix=issued.public_prefix,
        secret_digest=digest,
        status="ACTIVE",
        activated_at=datetime.now(UTC),
    )
    session.add(version)
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="API_KEY_CREATED",
        target_type="API_KEY",
        target_id=key.public_id,
        details={"version": 1, "scopes": scopes},
    )
    return key, version, issued


def rotate_api_key(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    key_public_id: str,
    pepper: str,
) -> tuple[APIKeyVersion, IssuedAPIKey]:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    key = session.scalar(
        select(APIKey)
        .where(
            APIKey.organisation_id == principal.organisation_id,
            APIKey.environment_id == environment.id,
            APIKey.public_id == key_public_id,
            APIKey.status == "ACTIVE",
        )
        .with_for_update()
    )
    if key is None:
        raise not_found("API key")
    next_version = (
        session.scalar(
            select(func.max(APIKeyVersion.version)).where(APIKeyVersion.api_key_id == key.id)
        )
        or 0
    ) + 1
    issued, digest = issue_api_key(
        pepper=pepper,
        environment_type=environment.environment_type,  # type: ignore[arg-type]
    )
    version = APIKeyVersion(
        organisation_id=principal.organisation_id,
        environment_id=environment.id,
        api_key_id=key.id,
        version=next_version,
        public_prefix=issued.public_prefix,
        secret_digest=digest,
        status="PENDING",
    )
    session.add(version)
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="API_KEY_ROTATED",
        target_type="API_KEY",
        target_id=key.public_id,
        details={"version": next_version},
    )
    return version, issued


def activate_api_key_version(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    key_public_id: str,
    version_number: int,
) -> None:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    key = session.scalar(
        select(APIKey)
        .where(
            APIKey.organisation_id == principal.organisation_id,
            APIKey.environment_id == environment.id,
            APIKey.public_id == key_public_id,
            APIKey.status == "ACTIVE",
        )
        .with_for_update()
    )
    if key is None:
        raise not_found("API key")
    versions = list(
        session.scalars(
            select(APIKeyVersion).where(APIKeyVersion.api_key_id == key.id).with_for_update()
        )
    )
    target = next((item for item in versions if item.version == version_number), None)
    if target is None or target.status != "PENDING":
        raise RelayPayError(
            code="INVALID_KEY_VERSION", message="Pending API key version not found", http_status=409
        )
    now = datetime.now(UTC)
    for item in versions:
        if item.status == "ACTIVE":
            item.status = "REVOKED"
            item.revoked_at = now
    target.status = "ACTIVE"
    target.activated_at = now
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="API_KEY_VERSION_ACTIVATED",
        target_type="API_KEY",
        target_id=key.public_id,
        details={"version": version_number},
    )


def revoke_api_key(
    session: Session, *, principal: Principal, environment_public_id: str, key_public_id: str
) -> None:
    require_organisation_admin(principal)
    environment = _environment(session, principal, environment_public_id)
    key = session.scalar(
        select(APIKey)
        .where(
            APIKey.organisation_id == principal.organisation_id,
            APIKey.environment_id == environment.id,
            APIKey.public_id == key_public_id,
        )
        .with_for_update()
    )
    if key is None:
        raise not_found("API key")
    now = datetime.now(UTC)
    key.status = "REVOKED"
    key.revoked_at = now
    for version in session.scalars(
        select(APIKeyVersion).where(APIKeyVersion.api_key_id == key.id).with_for_update()
    ):
        if version.status != "REVOKED":
            version.status = "REVOKED"
            version.revoked_at = now
    append_audit(
        session,
        principal=principal,
        environment_id=environment.id,
        action="API_KEY_REVOKED",
        target_type="API_KEY",
        target_id=key.public_id,
    )
