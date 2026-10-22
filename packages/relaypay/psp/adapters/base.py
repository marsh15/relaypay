"""Provider evidence shapes, independent of operation matching and finalization."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from relaypay.psp.types import EventOutcome


@dataclass(frozen=True, slots=True)
class ParsedPspEvent:
    provider: str
    # Some providers supply the event identity only in HTTP headers.
    event_id: str | None
    event_type: str
    payment_id: str | None
    refund_id: str | None
    amount: int
    currency: str
    outcome: EventOutcome
    decline_code: str | None
    # Provider envelope event time, distinct from the payment entity's creation time.
    occurred_at: datetime
    metadata: dict[str, Any] | list[Any]


@dataclass(frozen=True, slots=True)
class SettlementRow:
    reference: str
    payment_id: str | None
    refund_id: str | None
    amount: int
    fee: int
    gst: int
    currency: str
    settled_at: datetime


class PspAdapter(Protocol):
    def verify(
        self, headers: Mapping[str, str], raw_body: bytes, secret: str, now: datetime
    ) -> None: ...

    def parse_event(
        self, raw_body: bytes, *, headers: Mapping[str, str] | None = None
    ) -> ParsedPspEvent: ...

    def parse_settlement_report(self, csv_bytes: bytes) -> list[SettlementRow]: ...
