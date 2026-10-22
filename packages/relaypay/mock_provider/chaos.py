"""Seeded, replayable chaos for the provider simulator (M17).

Every decision is a pure function of ``(seed, stable key, aspect)``: a chaos run that fails can
be replayed exactly from its seed, whatever order its requests arrive in. One-shot fault
directives (``/control/faults``) take precedence over the profile.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.mock_provider.models import ProviderAccount, ProviderChaosProfile

BPS = 10_000


@dataclass(frozen=True, slots=True)
class ChaosSettings:
    seed: int
    error_before_bps: int = 0
    error_after_bps: int = 0
    lose_response_bps: int = 0
    slow_bps: int = 0
    slow_ms: int = 0
    pending_bps: int = 0
    decline_bps: int = 0
    webhook_drop_bps: int = 0
    webhook_duplicate_bps: int = 0
    webhook_max_delay_ms: int = 0


@dataclass(frozen=True, slots=True)
class EffectChaos:
    """What the profile decided for one stable key's first mutation."""

    error_before: bool  # 503 before anything is committed
    outcome: str  # SUCCEEDED | DECLINED | PENDING
    error_after: bool  # commit, then answer 503
    lose_response: bool  # commit, then the response never arrives
    slow_ms: int


@dataclass(frozen=True, slots=True)
class WebhookChaos:
    dropped: bool
    copies: int
    delay_ms: int


def draw(seed: int, stable_key: str, aspect: str) -> int:
    """A uniform integer in ``[0, 10000)`` fixed by ``(seed, stable_key, aspect)``."""
    digest = hashlib.sha256(f"{seed}|{stable_key}|{aspect}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % BPS


def _hit(profile: ProviderChaosProfile, stable_key: str, aspect: str, bps: int) -> bool:
    return draw(profile.seed, stable_key, aspect) < bps


def effect_chaos(profile: ProviderChaosProfile, stable_key: str) -> EffectChaos:
    outcome = "SUCCEEDED"
    if _hit(profile, stable_key, "pending", profile.pending_bps):
        outcome = "PENDING"
    elif _hit(profile, stable_key, "decline", profile.decline_bps):
        outcome = "DECLINED"
    slow = _hit(profile, stable_key, "slow", profile.slow_bps)
    return EffectChaos(
        error_before=_hit(profile, stable_key, "error_before", profile.error_before_bps),
        outcome=outcome,
        error_after=_hit(profile, stable_key, "error_after", profile.error_after_bps),
        lose_response=_hit(profile, stable_key, "lose_response", profile.lose_response_bps),
        slow_ms=profile.slow_ms if slow else 0,
    )


def webhook_chaos(profile: ProviderChaosProfile | None, stable_key: str) -> WebhookChaos:
    if profile is None:
        return WebhookChaos(dropped=False, copies=1, delay_ms=0)
    duplicate = _hit(profile, stable_key, "webhook_duplicate", profile.webhook_duplicate_bps)
    delay = draw(profile.seed, stable_key, "webhook_delay") * profile.webhook_max_delay_ms // BPS
    return WebhookChaos(
        dropped=_hit(profile, stable_key, "webhook_drop", profile.webhook_drop_bps),
        copies=2 if duplicate else 1,
        delay_ms=delay,
    )


def account_profile(session: Session, account_id: uuid.UUID) -> ProviderChaosProfile | None:
    return session.scalar(
        select(ProviderChaosProfile).where(ProviderChaosProfile.provider_account_id == account_id)
    )


def configure_chaos(
    factory: sessionmaker[Session], *, account_public_id: str, chaos: ChaosSettings | None
) -> None:
    """Install, replace (``chaos``) or remove (``None``) an account's chaos profile."""
    with factory() as session, session.begin():
        account = session.scalar(
            select(ProviderAccount).where(ProviderAccount.public_id == account_public_id)
        )
        if account is None:
            raise RelayPayError(
                code="PROVIDER_ACCOUNT_NOT_FOUND",
                message="Provider account was not found",
                http_status=404,
            )
        profile = account_profile(session, account.id)
        if chaos is None:
            if profile is not None:
                session.delete(profile)
            return
        if profile is None:
            profile = ProviderChaosProfile(provider_account_id=account.id, seed=chaos.seed)
            session.add(profile)
        for field in ChaosSettings.__dataclass_fields__:
            setattr(profile, field, getattr(chaos, field))
