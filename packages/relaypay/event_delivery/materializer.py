import uuid
from datetime import UTC, datetime

from sqlalchemy import exists, select, true
from sqlalchemy.orm import Session, sessionmaker

from relaypay.event_delivery.models import EventRecipient, WebhookDelivery
from relaypay.ids import new_public_id


def materialize_delivery_batch(
    session: Session,
    *,
    batch_size: int = 50,
    organisation_id: uuid.UUID | None = None,
) -> int:
    recipients = list(
        session.scalars(
            select(EventRecipient)
            .where(
                EventRecipient.organisation_id == organisation_id
                if organisation_id is not None
                else true(),
                ~exists(
                    select(WebhookDelivery.id).where(
                        WebhookDelivery.event_recipient_id == EventRecipient.id,
                        WebhookDelivery.replay_of_delivery_id.is_(None),
                    )
                ),
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
                environment_id=recipient.environment_id,
                event_recipient_id=recipient.id,
                status="PENDING",
                attempt_count=0,
                next_attempt_at=now,
            )
            for recipient in recipients
        ]
    )
    return len(recipients)


def materialize_deliveries(
    factory: sessionmaker[Session],
    *,
    batch_size: int = 50,
    organisation_id: uuid.UUID | None = None,
) -> int:
    with factory() as session, session.begin():
        return materialize_delivery_batch(
            session, batch_size=batch_size, organisation_id=organisation_id
        )
