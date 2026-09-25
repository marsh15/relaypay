"""Worker entry points for immutable pre-cutoff settlement forecasts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.merchant_balances.models import MerchantAccount
from relaypay.settlement_intelligence.models import SettlementPolicy
from relaypay.settlement_intelligence.service import ensure_daily_forecast


def run_forecast_batch(
    session_factory: sessionmaker[Session], *, now: datetime | None = None
) -> int:
    """Ensure one pre-cutoff forecast per active policy; concurrent runs are no-ops."""
    moment = now or datetime.now(UTC)
    created = 0
    with session_factory() as session, session.begin():
        accounts = [
            (row.organisation_id, row.environment_id, row.id)
            for row in session.execute(
                select(
                    MerchantAccount.organisation_id,
                    MerchantAccount.environment_id,
                    MerchantAccount.id,
                )
                .join(
                    SettlementPolicy,
                    and_(
                        SettlementPolicy.merchant_account_id == MerchantAccount.id,
                        SettlementPolicy.organisation_id == MerchantAccount.organisation_id,
                        SettlementPolicy.environment_id == MerchantAccount.environment_id,
                        SettlementPolicy.status == "ACTIVE",
                    ),
                )
                .where(MerchantAccount.status == "ACTIVE")
            ).all()
        ]
    for organisation_id, environment_id, merchant_account_id in accounts:
        try:
            with session_factory() as session, session.begin():
                forecast = ensure_daily_forecast(
                    session,
                    organisation_id=organisation_id,
                    environment_id=environment_id,
                    merchant_account_id=merchant_account_id,
                    now=moment,
                )
                created += 1 if forecast is not None else 0
        except IntegrityError:
            continue
    return created
