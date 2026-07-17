from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.event_delivery.models import EventRecipient, WebhookDelivery
from relaypay.ids import new_public_id


def materialize_delivery_batch(session: Session, *, batch_size: int = 50) -> int:
    recipients = list(
        session.scalars(
            select(EventRecipient)
            .where(
                ~exists(
                    select(WebhookDelivery.id).where(
                        WebhookDelivery.event_recipient_id == EventRecipient.id,
                        WebhookDelivery.replay_of_delivery_id.is_(None),
                    )
                )
            )
            .order_by(EventRecipient.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    now = datetime.now(UTC)
    session.add_all(
        [
            WebhookDelivery(
                public_id=new_public_id("del"),
                organisation_id=recipient.organisation_id,
                event_recipient_id=recipient.id,
                status="PENDING",
                attempt_count=0,
                next_attempt_at=now,
            )
            for recipient in recipients
        ]
    )
    return len(recipients)


def materialize_deliveries(factory: sessionmaker[Session], *, batch_size: int = 50) -> int:
    with factory() as session, session.begin():
        return materialize_delivery_batch(session, batch_size=batch_size)
