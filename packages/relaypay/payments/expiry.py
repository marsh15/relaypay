"""Expire authorised payments whose authorization hold lapsed without a capture."""

import uuid

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.payments.models import Authorization, Capture, PaymentIntent, Void
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
    child-row eligibility is rechecked in a fresh statement after the lock is acquired. This
    also protects a void admitted after the original locking query's snapshot.
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
                ~exists().where(
                    Void.payment_intent_id == PaymentIntent.id, Void.status != "FAILED"
                ),
            )
            .order_by(Authorization.expires_at, PaymentIntent.id)
            .limit(batch_size)
            .with_for_update(of=PaymentIntent, skip_locked=True)
        )
        if organisation_id is not None:
            statement = statement.where(PaymentIntent.organisation_id == organisation_id)
        payments = list(session.scalars(statement))
        expired = 0
        for payment in payments:
            in_flight = session.scalar(
                select(
                    or_(
                        exists().where(Capture.payment_intent_id == payment.id),
                        exists().where(
                            Void.payment_intent_id == payment.id, Void.status != "FAILED"
                        ),
                    )
                )
            )
            if in_flight:
                continue
            transition(
                session,
                payment,
                PaymentStatus.EXPIRED,
                reason="AUTHORIZATION_EXPIRED",
                actor=EXPIRY_ACTOR,
            )
            expired += 1
    return expired
