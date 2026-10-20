"""Expire authorised payments whose authorization hold lapsed without a capture."""

import uuid

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.payments.models import Authorization, Capture, PaymentIntent
from relaypay.payments.transitions import PaymentStatus, transition

EXPIRY_ACTOR = "EXPIRY_WORKER"


def expire_authorizations(
    factory: sessionmaker[Session],
    *,
    batch_size: int = 100,
    organisation_id: uuid.UUID | None = None,
) -> int:
    """Move ``authorised`` payments past ``expires_at`` with no capture to ``expired``.

    Each payment row is taken ``FOR UPDATE SKIP LOCKED``. ``initiate_capture`` takes the same
    payment lock first and refuses an expired hold, so a capture and an expiry for the same
    payment are serialized: whichever commits first wins, and the other sees its result. The
    no-capture condition is re-evaluated under the lock. A capture already in flight therefore
    always prevents expiry, and its outcome decides the payment.
    """
    with factory() as session, session.begin():
        statement = (
            select(PaymentIntent)
            .join(Authorization, Authorization.payment_intent_id == PaymentIntent.id)
            .where(
                PaymentIntent.status == PaymentStatus.AUTHORISED.value,
                Authorization.status == "SUCCEEDED",
                Authorization.expires_at <= func.now(),
                ~exists().where(Capture.payment_intent_id == PaymentIntent.id),
            )
            .order_by(Authorization.expires_at, PaymentIntent.id)
            .limit(batch_size)
            .with_for_update(of=PaymentIntent, skip_locked=True)
        )
        if organisation_id is not None:
            statement = statement.where(PaymentIntent.organisation_id == organisation_id)
        payments = list(session.scalars(statement))
        for payment in payments:
            transition(
                session,
                payment,
                PaymentStatus.EXPIRED,
                reason="AUTHORIZATION_EXPIRED",
                actor=EXPIRY_ACTOR,
            )
    return len(payments)
