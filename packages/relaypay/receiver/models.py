from datetime import datetime

from sqlalchemy import BigInteger, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from relaypay.receiver.database import ReceiverBase


class ReceivedEvent(ReceiverBase):
    __tablename__ = "received_events"
    __table_args__ = ({"schema": "receiver"},)

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_sha256: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    first_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_count: Mapped[int] = mapped_column(nullable=False, default=1)
    signature_timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
