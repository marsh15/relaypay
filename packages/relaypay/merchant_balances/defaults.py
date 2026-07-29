import uuid

from sqlalchemy import event, insert, select
from sqlalchemy.orm import Session

from relaypay.ids import new_public_id, new_uuid
from relaypay.ledger.models import LedgerAccount
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.payments.models import PaymentIntent

_DEFAULTS_INSTALLED = False
_ACCOUNT_TEMPLATES = (
    ("PENDING_PAYABLE_LIABILITY", "Pending merchant payable", "LIABILITY"),
    ("AVAILABLE_PAYABLE_LIABILITY", "Available merchant payable", "LIABILITY"),
    ("PAYOUT_CLEARING_ASSET", "Payout clearing", "ASSET"),
    ("MERCHANT_RECEIVABLE_ASSET", "Merchant receivable", "ASSET"),
)


def install_merchant_defaults() -> None:
    """Keep direct ORM payment construction compatible with the default account."""
    global _DEFAULTS_INSTALLED
    if _DEFAULTS_INSTALLED:
        return
    event.listen(Session, "before_flush", _assign_default_merchants)
    _DEFAULTS_INSTALLED = True


def _new_default(
    session: Session, organisation_id: uuid.UUID, environment_id: uuid.UUID
) -> uuid.UUID:
    merchant_id = new_uuid()
    session.connection().execute(
        insert(MerchantAccount).values(
            id=merchant_id,
            public_id=new_public_id("mac"),
            organisation_id=organisation_id,
            environment_id=environment_id,
            reference="default",
            name="Default",
            currency="INR",
            is_default=True,
            status="ACTIVE",
        )
    )
    session.connection().execute(
        insert(LedgerAccount),
        [
            {
                "id": new_uuid(),
                "organisation_id": organisation_id,
                "environment_id": environment_id,
                "merchant_account_id": merchant_id,
                "code": code,
                "name": name,
                "account_type": account_type,
                "currency": "INR",
            }
            for code, name, account_type in _ACCOUNT_TEMPLATES
        ],
    )
    return merchant_id


def _assign_default_merchants(session: Session, _flush_context: object, _instances: object) -> None:
    payments = [
        item
        for item in session.new
        if isinstance(item, PaymentIntent)
        and item.merchant_account_id is None
        and item.environment_id is not None
    ]
    if not payments:
        return

    pending = {
        (item.organisation_id, item.environment_id): item
        for item in session.new
        if isinstance(item, MerchantAccount) and item.is_default and item.status == "ACTIVE"
    }
    for payment in payments:
        key = (payment.organisation_id, payment.environment_id)
        pending_merchant = pending.get(key)
        merchant_id = pending_merchant.id if pending_merchant is not None else None
        if merchant_id is None:
            merchant_id = session.scalar(
                select(MerchantAccount.id).where(
                    MerchantAccount.organisation_id == payment.organisation_id,
                    MerchantAccount.environment_id == payment.environment_id,
                    MerchantAccount.is_default.is_(True),
                    MerchantAccount.status == "ACTIVE",
                )
            )
        if merchant_id is None:
            merchant_id = _new_default(session, *key)
        payment.merchant_account_id = merchant_id
