"""Razorpay webhook and settlement report parsing.

Razorpay signs the exact request bytes and does not include a signed delivery timestamp. The
adapter therefore checks the signed event's ``created_at`` against a replay window; that limits
replays of old events but cannot distinguish a legitimate retry from a replay of a recent event.
"""

import csv
import hashlib
import hmac
import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

from relaypay.errors import RelayPayError
from relaypay.psp.adapters.base import ParsedPspEvent, SettlementRow
from relaypay.psp.types import EventOutcome


class RazorpayAdapter:
    """Verify Razorpay raw-body HMACs and normalize documented payment/refund events."""

    _SIGNATURE_HEADER: ClassVar[str] = "x-razorpay-signature"
    _EVENT_ID_HEADER: ClassVar[str] = "x-razorpay-event-id"
    _EVENTS: ClassVar[dict[str, tuple[str, EventOutcome]]] = {
        "payment.authorized": ("payment", "SUCCEEDED"),
        "payment.captured": ("payment", "SUCCEEDED"),
        "payment.failed": ("payment", "DECLINED"),
        "refund.processed": ("refund", "SUCCEEDED"),
        "refund.failed": ("refund", "DECLINED"),
    }

    def __init__(self, replay_seconds: int = 300) -> None:
        if replay_seconds < 0:
            raise ValueError("replay_seconds must be non-negative")
        self.replay_seconds = replay_seconds

    def verify(
        self, headers: Mapping[str, str], raw_body: bytes, secret: str, now: datetime
    ) -> None:
        normalized = {name.lower(): value for name, value in headers.items()}
        signature = normalized.get(self._SIGNATURE_HEADER)
        event_id = normalized.get(self._EVENT_ID_HEADER)
        if not signature or not event_id:
            raise RelayPayError(
                "PSP_WEBHOOK_HEADERS_MISSING",
                "Razorpay signature and event id headers are required",
                401,
            )
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
            raise RelayPayError(
                "INVALID_WEBHOOK_SIGNATURE", "Razorpay webhook signature is invalid", 401
            )

        try:
            envelope = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RelayPayError("INVALID_PSP_EVENT", "Razorpay event is malformed", 400) from error
        if not isinstance(envelope, dict):
            raise RelayPayError("INVALID_PSP_EVENT", "Razorpay event must be an object", 400)
        created_at = envelope.get("created_at")
        if isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0:
            raise RelayPayError(
                "INVALID_WEBHOOK_TIMESTAMP", "Razorpay event timestamp is invalid", 401
            )
        try:
            event_time = datetime.fromtimestamp(created_at, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise RelayPayError(
                "INVALID_WEBHOOK_TIMESTAMP", "Razorpay event timestamp is invalid", 401
            ) from error
        comparison_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now
        if abs((comparison_now - event_time).total_seconds()) > self.replay_seconds:
            raise RelayPayError(
                "WEBHOOK_REPLAY_WINDOW_EXCEEDED",
                "Razorpay event is outside the configured replay window",
                401,
            )

    def parse_event(
        self, raw_body: bytes, *, headers: Mapping[str, str] | None = None
    ) -> ParsedPspEvent:
        envelope = _decode_json(raw_body)
        event_type = envelope.get("event")
        if not isinstance(event_type, str):
            raise _invalid_event("Razorpay event type is missing")
        mapping = self._EVENTS.get(event_type)
        if mapping is None:
            raise RelayPayError(
                "PSP_EVENT_UNSUPPORTED", f"Razorpay event {event_type!r} is unsupported", 400
            )
        entity_name, outcome = mapping
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get(entity_name), dict):
            raise _invalid_event(f"Razorpay {entity_name} payload is missing")
        wrapper = payload[entity_name]
        entity = wrapper.get("entity")
        if not isinstance(entity, dict):
            raise _invalid_event(f"Razorpay {entity_name} entity is missing")

        identifier = _string_field(entity, "id")
        amount = _integer_field(entity, "amount")
        currency = _string_field(entity, "currency")
        created_at = envelope.get("created_at")
        if isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0:
            raise RelayPayError(
                "INVALID_WEBHOOK_TIMESTAMP", "Razorpay event timestamp is invalid", 400
            )
        try:
            occurred_at = datetime.fromtimestamp(created_at, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise RelayPayError(
                "INVALID_WEBHOOK_TIMESTAMP", "Razorpay event timestamp is invalid", 400
            ) from error
        event_id: str | None = None
        if headers is not None:
            normalized_headers = {name.lower(): value for name, value in headers.items()}
            event_id = normalized_headers.get(self._EVENT_ID_HEADER)

        payment_id = (
            identifier if entity_name == "payment" else _optional_string(entity.get("payment_id"))
        )
        refund_id = identifier if entity_name == "refund" else None
        decline_code = _optional_string(entity.get("error_code")) if outcome == "DECLINED" else None
        notes: Any = entity.get("notes", {})
        if not isinstance(notes, dict | list):
            raise _invalid_event("Razorpay notes must be an object or array")
        return ParsedPspEvent(
            provider="RAZORPAY",
            event_id=event_id,
            event_type=event_type,
            payment_id=payment_id,
            refund_id=refund_id,
            amount=amount,
            currency=currency,
            outcome=outcome,
            decline_code=decline_code,
            occurred_at=occurred_at,
            metadata=notes,
        )

    def parse_settlement_report(self, csv_bytes: bytes) -> list[SettlementRow]:
        """Parse the adapter's documented normalized report columns, all amounts in paise.

        Razorpay public docs describe downloadable settlement reports and payment entity ``fees``
        and ``tax`` values but do not specify one stable CSV schema. This parser's header mapping is
        consequently an assumed schema; the fixture README records that maintainers must confirm it.
        """
        try:
            text = csv_bytes.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            required = {
                "reference",
                "payment_id",
                "refund_id",
                "amount",
                "fee",
                "tax",
                "currency",
                "settled_at",
            }
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise _invalid_row(1, "required columns are missing")
            rows: list[SettlementRow] = []
            try:
                for row in reader:
                    try:
                        if None in row or any(value is None for value in row.values()):
                            raise ValueError("column count does not match header")
                        reference = _csv_required(row, "reference")
                        payment_id = _csv_optional(row, "payment_id")
                        refund_id = _csv_optional(row, "refund_id")
                        if payment_id is None and refund_id is None:
                            raise ValueError("payment_id or refund_id is required")
                        amount = _csv_amount(row, "amount")
                        fee = _csv_amount(row, "fee")
                        tax = _csv_amount(row, "tax")
                        currency = _csv_required(row, "currency")
                        if len(currency) != 3 or not currency.isalpha():
                            raise ValueError("currency must be a three-letter code")
                        settled_text = _csv_required(row, "settled_at")
                        settled_at = datetime.fromisoformat(settled_text.replace("Z", "+00:00"))
                        if settled_at.tzinfo is None:
                            settled_at = settled_at.replace(tzinfo=UTC)
                    except (ValueError, KeyError) as error:
                        raise _invalid_row(reader.reader.line_num, str(error)) from error
                    rows.append(
                        SettlementRow(
                            reference,
                            payment_id,
                            refund_id,
                            amount,
                            fee,
                            tax,
                            currency.upper(),
                            settled_at,
                        )
                    )
            except csv.Error as error:
                raise _invalid_row(reader.reader.line_num, "CSV is malformed") from error
            return rows
        except UnicodeDecodeError as error:
            raise _invalid_row(1, "CSV is malformed") from error


def _decode_json(raw_body: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _invalid_event("Razorpay event is malformed") from error
    if not isinstance(decoded, dict):
        raise _invalid_event("Razorpay event must be an object")
    return decoded


def _invalid_event(message: str) -> RelayPayError:
    return RelayPayError("INVALID_PSP_EVENT", message, 400)


def _invalid_row(line_number: int, message: str) -> RelayPayError:
    return RelayPayError(
        "SETTLEMENT_ROW_INVALID",
        "Razorpay settlement CSV row is malformed",
        400,
        {"line": line_number, "reason": message},
    )


def _string_field(entity: dict[str, Any], field: str) -> str:
    value = entity.get(field)
    if not isinstance(value, str) or not value:
        raise _invalid_event(f"Razorpay {field} is missing")
    return value


def _integer_field(entity: dict[str, Any], field: str) -> int:
    value = entity.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid_event(f"Razorpay {field} must be a non-negative integer")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid_event("Razorpay identifier or decline code must be a string")
    return value


def _csv_required(row: dict[str, str | None], field: str) -> str:
    value = row.get(field)
    if value is None or value == "":
        raise ValueError(f"{field} is required")
    return value


def _csv_optional(row: dict[str, str | None], field: str) -> str | None:
    value = row.get(field)
    return value or None


def _csv_amount(row: dict[str, str | None], field: str) -> int:
    value = _csv_required(row, field)
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{field} must be a non-negative integer number of paise")
    return int(value)
