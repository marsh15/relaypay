"""Fee schedule versions and the capture-time fee split."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError
from relaypay.fees.calculator import compute_capture_fee
from relaypay.fees.models import FeeScheduleVersion
from relaypay.fees.types import FeeBreakdown, FeeScheduleTerms, RefundFeePolicy
from relaypay.ids import new_public_id, new_uuid
from relaypay.merchant_balances.models import MerchantAccount

FeeCalculator = Callable[[int, FeeScheduleTerms], FeeBreakdown]


@dataclass(frozen=True, slots=True)
class CaptureFee:
    """The fee split stored on a capture at initiation, so recovery never recomputes it."""

    fee_amount: int
    gst_amount: int
    fee_schedule_version_id: uuid.UUID | None


NO_FEE = CaptureFee(0, 0, None)


def terms_of(version: FeeScheduleVersion) -> FeeScheduleTerms:
    policy: RefundFeePolicy = "RETAIN"
    if version.refund_fee_policy != policy:
        raise RuntimeError(f"unsupported refund fee policy {version.refund_fee_policy}")
    return FeeScheduleTerms(
        mdr_bps=version.mdr_bps,
        fixed_paise=version.fixed_paise,
        gst_bps=version.gst_bps,
        min_fee_paise=version.min_fee_paise,
        max_fee_paise=version.max_fee_paise,
        refund_fee_policy=policy,
    )


def active_fee_schedule(
    session: Session, merchant_account_id: uuid.UUID
) -> FeeScheduleVersion | None:
    """The newest version already in effect (database time), or ``None`` for no fees."""
    return session.scalar(
        select(FeeScheduleVersion)
        .where(
            FeeScheduleVersion.merchant_account_id == merchant_account_id,
            FeeScheduleVersion.effective_from <= func.now(),
        )
        .order_by(FeeScheduleVersion.version.desc())
        .limit(1)
    )


def capture_fee(
    session: Session,
    *,
    merchant_account_id: uuid.UUID,
    amount: int,
    calculator: FeeCalculator = compute_capture_fee,
) -> CaptureFee:
    """Split a capture by the merchant's active schedule; no schedule means no fee."""
    schedule = active_fee_schedule(session, merchant_account_id)
    if schedule is None:
        return NO_FEE
    breakdown = calculator(amount, terms_of(schedule))
    if (
        breakdown.fee < 0
        or breakdown.gst < 0
        or breakdown.net < 0
        or breakdown.fee + breakdown.gst + breakdown.net != amount
    ):
        raise RuntimeError("fee calculator returned an inconsistent split")
    return CaptureFee(breakdown.fee, breakdown.gst, schedule.id)


def create_fee_schedule_version(
    session: Session,
    *,
    merchant_account_id: uuid.UUID,
    effective_from: datetime,
    mdr_bps: int,
    fixed_paise: int,
    gst_bps: int = 1800,
    min_fee_paise: int | None = None,
    max_fee_paise: int | None = None,
) -> FeeScheduleVersion:
    """Append the next immutable version (locks the merchant account to number versions)."""
    merchant = session.scalar(
        select(MerchantAccount).where(MerchantAccount.id == merchant_account_id).with_for_update()
    )
    if merchant is None:
        raise RelayPayError(code="NOT_FOUND", message="Merchant account not found", http_status=404)
    latest = session.scalar(
        select(func.max(FeeScheduleVersion.version)).where(
            FeeScheduleVersion.merchant_account_id == merchant_account_id
        )
    )
    version = FeeScheduleVersion(
        id=new_uuid(),
        public_id=new_public_id("fee"),
        organisation_id=merchant.organisation_id,
        environment_id=merchant.environment_id,
        merchant_account_id=merchant_account_id,
        version=int(latest or 0) + 1,
        effective_from=effective_from,
        mdr_bps=mdr_bps,
        fixed_paise=fixed_paise,
        gst_bps=gst_bps,
        min_fee_paise=min_fee_paise,
        max_fee_paise=max_fee_paise,
        refund_fee_policy="RETAIN",
    )
    session.add(version)
    session.flush([version])
    return version
