"""The persisted payment status machine and its single writer.

``payment_intents.status`` is changed only by :func:`transition` (and set once at creation by
:func:`record_creation`). A BEFORE UPDATE OF status trigger created by migration 0019 enforces
the same table in SQL, so a raw UPDATE cannot make an illegal move either. The migration inlines
the table as SQL VALUES and ``tests/unit/test_transitions.py`` fails if it drifts from
:data:`ALLOWED`.

Capture decline maps to ``failed`` (``authorised -> failed``), not to "stay authorised": the
capture is a singleton child of the payment (UNIQUE on ``payment_intent_id``), so a FAILED
capture is terminal today, exactly like a FAILED authorization. Leaving the payment authorised
would advertise a capture that can never be retried. If a later task makes capture retryable,
that task decides the mapping again.

``requires_action`` is reachable (``created -> requires_action``) but unused: the provider
result exposes no "next action" field yet. M17 (UPI collect) will move payments there.
"""

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError
from relaypay.ids import new_uuid
from relaypay.payments.models import PaymentIntent, PaymentStatusHistory


class PaymentStatus(StrEnum):
    CREATED = "created"
    REQUIRES_ACTION = "requires_action"
    AUTHORISED = "authorised"
    CAPTURED = "captured"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_S = PaymentStatus

ALLOWED: frozenset[tuple[str, str]] = frozenset(
    (source.value, target.value)
    for source, target in (
        (_S.CREATED, _S.REQUIRES_ACTION),
        (_S.CREATED, _S.AUTHORISED),
        (_S.CREATED, _S.FAILED),
        (_S.CREATED, _S.CANCELLED),
        (_S.REQUIRES_ACTION, _S.AUTHORISED),
        (_S.REQUIRES_ACTION, _S.FAILED),
        (_S.REQUIRES_ACTION, _S.CANCELLED),
        (_S.REQUIRES_ACTION, _S.EXPIRED),
        (_S.AUTHORISED, _S.CAPTURED),
        (_S.AUTHORISED, _S.CANCELLED),
        (_S.AUTHORISED, _S.EXPIRED),
        (_S.AUTHORISED, _S.FAILED),  # capture permanently declined
        (_S.CAPTURED, _S.PARTIALLY_REFUNDED),
        (_S.CAPTURED, _S.REFUNDED),
        (_S.PARTIALLY_REFUNDED, _S.PARTIALLY_REFUNDED),
        (_S.PARTIALLY_REFUNDED, _S.REFUNDED),
    )
)

CREATION_REASON = "PAYMENT_CREATED"
BACKFILL_REASON = "BACKFILL_V2"


def is_allowed(source: str, target: str) -> bool:
    return (source, target) in ALLOWED


def record_creation(session: Session, payment: PaymentIntent, *, actor: str) -> None:
    """Stamp a brand-new payment ``created`` and write its initial (version 0) history row.

    Every payment's history starts with exactly one row whose ``from_status`` is NULL and
    ``status_version`` is 0: this one, or the ``BACKFILL_V2`` row for payments that predate
    migration 0019. Hence ``count(history) == status_version + 1`` always.
    """
    payment.status = PaymentStatus.CREATED.value
    payment.status_version = 0
    session.add(
        PaymentStatusHistory(
            id=new_uuid(),
            organisation_id=payment.organisation_id,
            environment_id=payment.environment_id,
            payment_intent_id=payment.id,
            from_status=None,
            to_status=PaymentStatus.CREATED.value,
            reason=CREATION_REASON,
            actor=actor,
            status_version=0,
        )
    )


def _lock_versions(session: Session, payment_id: uuid.UUID) -> tuple[str, int]:
    row = session.execute(
        select(PaymentIntent.status, PaymentIntent.status_version)
        .where(PaymentIntent.id == payment_id)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise RuntimeError("payment intent vanished while transitioning")
    return str(row[0]), int(row[1])


def transition(
    session: Session,
    payment: PaymentIntent,
    to: PaymentStatus,
    *,
    reason: str,
    actor: str,
) -> None:
    """Move ``payment`` to ``to``: validate, bump ``status_version``, append history.

    Precondition: the caller already holds ``SELECT ... FOR UPDATE`` on the payment row (the
    finalizer takes it first in ``_locked_graph``; ``payments.service._locked_payment`` does the
    same), so the payment lock precedes every operation and resource lock. This function
    re-issues ``FOR UPDATE`` on the row, which is a no-op for the lock holder, and refuses to
    write when the in-memory ``status`` or ``status_version`` is stale, so a caller that forgot
    to lock cannot silently overwrite a concurrent transition.

    Raises ``RelayPayError`` ``ILLEGAL_PAYMENT_TRANSITION`` for a pair outside :data:`ALLOWED`
    and ``PAYMENT_STATE_STALE`` for a stale view.
    """
    current, version = _lock_versions(session, payment.id)
    if current != payment.status or version != payment.status_version:
        raise RelayPayError(
            code="PAYMENT_STATE_STALE",
            message="The payment changed since it was loaded; reload it under the payment lock",
            http_status=409,
            details={"expectedStatus": payment.status, "actualStatus": current},
        )
    if not is_allowed(current, to.value):
        raise RelayPayError(
            code="ILLEGAL_PAYMENT_TRANSITION",
            message=f"Payment cannot move from {current} to {to.value}",
            http_status=409,
            details={"from": current, "to": to.value},
        )
    payment.status = to.value
    payment.status_version = version + 1
    session.add(
        PaymentStatusHistory(
            id=new_uuid(),
            organisation_id=payment.organisation_id,
            environment_id=payment.environment_id,
            payment_intent_id=payment.id,
            from_status=current,
            to_status=to.value,
            reason=reason,
            actor=actor,
            status_version=version + 1,
        )
    )
