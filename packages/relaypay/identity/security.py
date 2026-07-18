import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError
from relaypay.idempotency import digest_secret
from relaypay.identity.models import (
    APIKey,
    APIKeyVersion,
    Environment,
    Organisation,
    OrganisationMembership,
    SessionRecord,
    User,
)
from relaypay.ids import new_uuid

_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=2)
_SESSION_TTL = timedelta(hours=8)


@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["SESSION", "API_KEY"]
    organisation_id: uuid.UUID
    organisation_public_id: str
    environment_id: uuid.UUID | None
    environment_public_id: str | None
    display_name: str
    scopes: frozenset[str]
    platform_role: str = "STANDARD"
    membership_role: str | None = None
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime
    principal: Principal


@dataclass(frozen=True, slots=True)
class IssuedAPIKey:
    plaintext: str
    public_prefix: str


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, candidate: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, candidate)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _token_digest(token: str, secret: str) -> bytes:
    return digest_secret(token, secret)


def _csrf_digest(session_id: uuid.UUID, token: str, secret: str) -> bytes:
    return digest_secret(f"{session_id}:{token}", secret)


def issue_session(
    session: Session,
    *,
    email: str,
    password: str,
    session_secret: str,
    csrf_secret: str,
    organisation_public_id: str | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    normalized = email.strip().casefold()
    users = session.scalars(
        select(User).where(User.email_normalized == normalized, User.status == "ACTIVE")
    ).all()
    if len(users) != 1 or not verify_password(users[0].password_hash, password):
        raise RelayPayError(
            code="INVALID_CREDENTIALS",
            message="Email or password is incorrect",
            http_status=401,
        )

    user = users[0]
    membership_query = (
        select(OrganisationMembership, Organisation)
        .join(Organisation, Organisation.id == OrganisationMembership.organisation_id)
        .where(
            OrganisationMembership.user_id == user.id,
            OrganisationMembership.status == "ACTIVE",
            Organisation.status == "ACTIVE",
        )
    )
    if organisation_public_id is not None:
        membership_query = membership_query.where(Organisation.public_id == organisation_public_id)
    memberships = session.execute(membership_query).all()
    if len(memberships) != 1:
        raise RelayPayError(
            code="ORGANISATION_CONTEXT_REQUIRED",
            message="Select exactly one organisation context",
            http_status=409,
        )
    membership, organisation = memberships[0]

    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session_id = new_uuid()
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + _SESSION_TTL
    session.add(
        SessionRecord(
            id=session_id,
            organisation_id=organisation.id,
            user_id=user.id,
            token_digest=_token_digest(token, session_secret),
            csrf_digest=_csrf_digest(session_id, csrf_token, csrf_secret),
            expires_at=expires_at,
        )
    )
    return IssuedSession(
        token=token,
        csrf_token=csrf_token,
        expires_at=expires_at,
        principal=Principal(
            kind="SESSION",
            organisation_id=organisation.id,
            organisation_public_id=organisation.public_id,
            environment_id=None,
            environment_public_id=None,
            display_name=user.display_name,
            scopes=_membership_scopes(membership.role),
            platform_role=user.platform_role,
            membership_role=membership.role,
            user_id=user.id,
            session_id=session_id,
        ),
    )


def authenticate_session(
    session: Session,
    *,
    token: str,
    session_secret: str,
    now: datetime | None = None,
) -> Principal:
    record = session.scalar(
        select(SessionRecord).where(
            SessionRecord.token_digest == _token_digest(token, session_secret)
        )
    )
    current_time = now or datetime.now(UTC)
    if record is None or record.revoked_at is not None or record.expires_at <= current_time:
        raise RelayPayError(
            code="UNAUTHENTICATED", message="Authentication required", http_status=401
        )
    user = session.scalar(select(User).where(User.id == record.user_id, User.status == "ACTIVE"))
    membership = session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == record.user_id,
            OrganisationMembership.organisation_id == record.organisation_id,
            OrganisationMembership.status == "ACTIVE",
        )
    )
    organisation = session.scalar(
        select(Organisation).where(
            Organisation.id == record.organisation_id,
            Organisation.status == "ACTIVE",
        )
    )
    if user is None or membership is None or organisation is None:
        raise RelayPayError(
            code="UNAUTHENTICATED", message="Authentication required", http_status=401
        )
    return Principal(
        kind="SESSION",
        organisation_id=organisation.id,
        organisation_public_id=organisation.public_id,
        environment_id=None,
        environment_public_id=None,
        display_name=user.display_name,
        scopes=_membership_scopes(membership.role),
        platform_role=user.platform_role,
        membership_role=membership.role,
        user_id=user.id,
        session_id=record.id,
    )


def rotate_csrf(session: Session, principal: Principal, csrf_secret: str) -> str:
    if principal.kind != "SESSION" or principal.session_id is None:
        raise RelayPayError(
            code="FORBIDDEN", message="Administrator session required", http_status=403
        )
    record = session.get(SessionRecord, principal.session_id)
    if record is None or record.revoked_at is not None:
        raise RelayPayError(
            code="UNAUTHENTICATED", message="Authentication required", http_status=401
        )
    csrf_token = secrets.token_urlsafe(32)
    record.csrf_digest = _csrf_digest(record.id, csrf_token, csrf_secret)
    record.last_seen_at = datetime.now(UTC)
    return csrf_token


def verify_csrf(
    session: Session, *, principal: Principal, csrf_token: str | None, csrf_secret: str
) -> None:
    if principal.kind != "SESSION" or principal.session_id is None:
        raise RelayPayError(
            code="FORBIDDEN", message="Administrator session required", http_status=403
        )
    record = session.get(SessionRecord, principal.session_id)
    if (
        record is None
        or csrf_token is None
        or not hmac.compare_digest(
            record.csrf_digest, _csrf_digest(record.id, csrf_token, csrf_secret)
        )
    ):
        raise RelayPayError(
            code="CSRF_INVALID", message="CSRF token is missing or invalid", http_status=403
        )


def revoke_session(session: Session, principal: Principal) -> None:
    if principal.session_id is None:
        return
    record = session.get(SessionRecord, principal.session_id)
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)


def issue_api_key(
    *, pepper: str, environment_type: Literal["TEST", "LIVE_LIKE"] = "TEST"
) -> tuple[IssuedAPIKey, bytes]:
    prefix_entropy = base64.b32encode(secrets.token_bytes(5)).decode("ascii").rstrip("=").lower()
    marker = "test" if environment_type == "TEST" else "live_like"
    public_prefix = f"rpk_{marker}_{prefix_entropy}"
    secret = secrets.token_urlsafe(32)
    plaintext = f"{public_prefix}.{secret}"
    return IssuedAPIKey(plaintext=plaintext, public_prefix=public_prefix), digest_secret(
        plaintext, pepper
    )


def authenticate_api_key(session: Session, *, plaintext: str, pepper: str) -> Principal:
    if "." not in plaintext:
        raise RelayPayError(
            code="UNAUTHENTICATED", message="Authentication required", http_status=401
        )
    public_prefix = plaintext.split(".", 1)[0]
    row = session.execute(
        select(APIKey, APIKeyVersion, Organisation, Environment)
        .join(APIKeyVersion, APIKeyVersion.api_key_id == APIKey.id)
        .join(Organisation, Organisation.id == APIKey.organisation_id)
        .join(Environment, Environment.id == APIKey.environment_id)
        .where(
            APIKeyVersion.public_prefix == public_prefix,
            APIKeyVersion.status == "ACTIVE",
            APIKey.status == "ACTIVE",
            Organisation.status == "ACTIVE",
            Environment.status == "ACTIVE",
        )
    ).one_or_none()
    candidate = digest_secret(plaintext, pepper)
    if row is None or not hmac.compare_digest(row[1].secret_digest, candidate):
        raise RelayPayError(
            code="UNAUTHENTICATED", message="Authentication required", http_status=401
        )
    record, version, organisation, environment = row
    version.last_used_at = datetime.now(UTC)
    return Principal(
        kind="API_KEY",
        organisation_id=organisation.id,
        organisation_public_id=organisation.public_id,
        environment_id=environment.id,
        environment_public_id=environment.public_id,
        display_name=record.name,
        scopes=frozenset(record.scopes),
        api_key_id=record.id,
    )


def require_scopes(principal: Principal, *scopes: str) -> None:
    missing = set(scopes) - principal.scopes
    if missing:
        raise RelayPayError(
            code="FORBIDDEN",
            message="The authenticated principal lacks the required permission",
            http_status=403,
        )


def _membership_scopes(role: str) -> frozenset[str]:
    if role == "ORGANISATION_ADMIN":
        return frozenset({"admin", "members:write", "keys:write", "operations:write"})
    if role == "DEVELOPER":
        return frozenset({"technical:read", "financial:read"})
    return frozenset({"technical:read", "financial:read"})


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
