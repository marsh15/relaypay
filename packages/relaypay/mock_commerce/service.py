import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.ids import new_public_id
from relaypay.mock_commerce.models import CommerceAccount, CommerceOrder, CommercePaymentLink


def create_order(
    factory: sessionmaker[Session],
    *,
    account_public_id: str,
    external_reference: str,
    total_amount: int,
) -> CommerceOrder:
    with factory() as session, session.begin():
        account = session.scalar(
            select(CommerceAccount).where(CommerceAccount.public_id == account_public_id)
        )
        if account is None:
            raise RelayPayError("COMMERCE_ACCOUNT_NOT_FOUND", "Commerce account not found", 404)
        existing = session.scalar(
            select(CommerceOrder).where(
                CommerceOrder.commerce_account_id == account.id,
                CommerceOrder.external_reference == external_reference,
            )
        )
        if existing is not None:
            if existing.total_amount != total_amount:
                raise RelayPayError(
                    "COMMERCE_ORDER_CONFLICT",
                    "Order reference is bound to a different total",
                    409,
                )
            return existing
        order = CommerceOrder(
            public_id=new_public_id("ord"),
            commerce_account_id=account.id,
            external_reference=external_reference,
            total_amount=total_amount,
            currency="INR",
            status="OPEN",
        )
        session.add(order)
        return order


def link_payment(
    factory: sessionmaker[Session],
    *,
    order_public_id: str,
    relaypay_payment_id: str,
    amount: int,
) -> None:
    with factory() as session, session.begin():
        order = session.scalar(
            select(CommerceOrder)
            .where(CommerceOrder.public_id == order_public_id)
            .with_for_update()
        )
        if order is None:
            raise RelayPayError("COMMERCE_ORDER_NOT_FOUND", "Commerce order not found", 404)
        linked_total = int(
            session.scalar(
                select(func.coalesce(func.sum(CommercePaymentLink.linked_amount), 0)).where(
                    CommercePaymentLink.commerce_order_id == order.id
                )
            )
            or 0
        )
        if linked_total + amount > order.total_amount:
            raise RelayPayError(
                "COMMERCE_TOTAL_MISMATCH",
                "Linked payment total exceeds the order total",
                409,
            )
        session.add(
            CommercePaymentLink(
                commerce_order_id=order.id,
                relaypay_payment_id=relaypay_payment_id,
                linked_amount=amount,
                status="LINKED",
            )
        )


def synchronize_event(
    factory: sessionmaker[Session], payload: bytes, provider_event_id: str
) -> None:
    data = json.loads(payload)
    payment_id = str(data["paymentId"])
    event_type = str(data["type"])
    with factory() as session, session.begin():
        link = session.scalar(
            select(CommercePaymentLink)
            .where(CommercePaymentLink.relaypay_payment_id == payment_id)
            .with_for_update()
        )
        if link is None:
            raise RelayPayError(
                "COMMERCE_PAYMENT_LINK_NOT_FOUND",
                "Payment link was not found",
                404,
                details={"providerEventId": provider_event_id},
            )
        order = session.get(CommerceOrder, link.commerce_order_id)
        if order is None:
            raise RuntimeError("commerce order graph is missing")
        if event_type == "payment.captured.v1":
            link.status = "PAID"
            paid = int(
                session.scalar(
                    select(func.coalesce(func.sum(CommercePaymentLink.linked_amount), 0)).where(
                        CommercePaymentLink.commerce_order_id == order.id,
                        CommercePaymentLink.status == "PAID",
                    )
                )
                or 0
            )
            if paid == order.total_amount:
                order.status = "PAID"
        elif event_type == "refund.succeeded.v1":
            link.status = "REFUNDED"
            order.status = "REFUNDED"
        else:
            raise RelayPayError(
                "COMMERCE_EVENT_UNSUPPORTED",
                "Commerce synchronization does not support this event type",
                422,
            )
