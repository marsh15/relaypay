"""Cashfree PG webhook and settlement-report normalization."""

import base64
import csv
import hashlib
import hmac
import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from relaypay.errors import RelayPayError
from relaypay.psp.adapters.base import ParsedPspEvent, SettlementRow
from relaypay.psp.types import EventOutcome

MAX_AMOUNT_RUPEES = Decimal("92233720368547758.07")


def _error(
    code: str, message: str, status: int = 400, details: dict[str, Any] | None = None
) -> RelayPayError:
    return RelayPayError(code=code, message=message, http_status=status, details=details or {})


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.lower() == name), None)


def _timestamp(value: str, *, milliseconds: bool = False) -> datetime:
    if milliseconds:
        if not value.isascii() or not value.isdigit() or len(value) > 13:
            raise ValueError("timestamp must be a UNIX millisecond integer")
        seconds, milliseconds_part = divmod(int(value), 1000)
        return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(milliseconds=milliseconds_part)
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, OSError) as error:
        raise ValueError("timestamp is outside the supported range") from error


def _rupees_to_paise(value: Any, field: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError(f"{field} must be a decimal amount")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a decimal amount") from error
    if (
        not amount.is_finite()
        or amount < 0
        or (amount == 0 and not allow_zero)
        or amount > MAX_AMOUNT_RUPEES
    ):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be {qualifier} within the supported paise range")
    if amount == 0:
        return 0
    if amount < Decimal("0.01"):
        raise ValueError(f"{field} must have at most two decimal places")
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + 2)
        paise = amount * 100
    if paise != paise.to_integral_value():
        raise ValueError(f"{field} must have at most two decimal places")
    return int(paise)


class CashfreeAdapter:
    """Verify Cashfree's millisecond timestamp signature and normalize supported events."""

    provider = "CASHFREE"

    def __init__(self, replay_seconds: int = 300) -> None:
        if replay_seconds <= 0:
            raise ValueError("replay_seconds must be positive")
        self.replay_seconds = replay_seconds

    def verify(
        self, headers: Mapping[str, str], raw_body: bytes, secret: str, now: datetime
    ) -> None:
        timestamp = _header(headers, "x-webhook-timestamp")
        signature = _header(headers, "x-webhook-signature")
        if not timestamp or not signature:
            raise _error(
                "PSP_WEBHOOK_HEADERS_MISSING", "Webhook authentication headers missing", 401
            )
        try:
            signed_at = _timestamp(timestamp, milliseconds=True)
        except (ValueError, OverflowError, OSError):
            raise _error("INVALID_WEBHOOK_TIMESTAMP", "Webhook timestamp is invalid", 401) from None
        if (
            now.tzinfo is None
            or abs((now.astimezone(UTC) - signed_at).total_seconds()) > self.replay_seconds
        ):
            raise _error(
                "WEBHOOK_REPLAY_WINDOW_EXCEEDED",
                "Webhook timestamp is outside the replay window",
                401,
            )
        expected = base64.b64encode(
            hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()
        ).decode("ascii")
        try:
            supplied = signature.encode("ascii")
        except UnicodeEncodeError:
            supplied = b""
        if not hmac.compare_digest(supplied, expected.encode("ascii")):
            raise _error("INVALID_WEBHOOK_SIGNATURE", "Webhook signature is invalid", 401)

    def parse_event(
        self, raw_body: bytes, *, headers: Mapping[str, str] | None = None
    ) -> ParsedPspEvent:
        try:
            payload = json.loads(raw_body, parse_float=Decimal, parse_int=Decimal)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise ValueError("data must be an object")
            event_type = payload.get("type")
            data = payload["data"]
            occurred_at = _timestamp(payload["event_time"])
            event_id = _header(headers or {}, "x-idempotency-key") or None
            if event_type in {
                "PAYMENT_SUCCESS_WEBHOOK",
                "PAYMENT_FAILED_WEBHOOK",
                "PAYMENT_USER_DROPPED_WEBHOOK",
            }:
                order, payment = data.get("order"), data.get("payment")
                if not isinstance(order, dict) or not isinstance(payment, dict):
                    raise ValueError("payment event requires order and payment objects")
                status = payment.get("payment_status")
                expected_status = {
                    "PAYMENT_SUCCESS_WEBHOOK": "SUCCESS",
                    "PAYMENT_FAILED_WEBHOOK": "FAILED",
                    "PAYMENT_USER_DROPPED_WEBHOOK": "USER_DROPPED",
                }[event_type]
                if status != expected_status:
                    raise ValueError("payment status conflicts with event type")
                amount = _rupees_to_paise(payment.get("payment_amount"), "payment_amount")
                currency = payment.get("payment_currency")
                if currency != "INR" or order.get("order_currency") != "INR":
                    raise ValueError("only INR events are supported")
                payment_id = payment.get("cf_payment_id")
                _timestamp(payment["payment_time"])
                if not isinstance(payment_id, str) or not payment_id:
                    raise ValueError("cf_payment_id is required")
                outcome: EventOutcome = "SUCCEEDED" if status == "SUCCESS" else "DECLINED"
                error_details = data.get("error_details")
                decline_code = None
                if isinstance(error_details, dict):
                    raw_code = error_details.get("error_code")
                    decline_code = raw_code if isinstance(raw_code, str) else None
                return ParsedPspEvent(
                    self.provider,
                    event_id,
                    event_type,
                    payment_id,
                    None,
                    amount,
                    "INR",
                    outcome,
                    decline_code,
                    occurred_at,
                    data,
                )
            if event_type in {"REFUND_STATUS_WEBHOOK", "AUTO_REFUND_STATUS_WEBHOOK"}:
                refund_key = (
                    "auto_refund" if event_type == "AUTO_REFUND_STATUS_WEBHOOK" else "refund"
                )
                refund = data.get(refund_key)
                if not isinstance(refund, dict):
                    raise ValueError("refund event requires refund object")
                status = refund.get("refund_status")
                if status not in {"SUCCESS", "FAILED", "CANCELLED", "PENDING"}:
                    raise ValueError("refund status conflicts with supported statuses")
                amount = _rupees_to_paise(refund.get("refund_amount"), "refund_amount")
                refund_id = refund.get("cf_refund_id") or refund.get("refund_id")
                payment_id = refund.get("cf_payment_id")
                if isinstance(refund_id, Decimal):
                    if (
                        not refund_id.is_finite()
                        or refund_id <= 0
                        or refund_id > Decimal(2**63 - 1)
                        or refund_id != refund_id.to_integral_value()
                    ):
                        raise ValueError("cf_refund_id must be a positive integer")
                    refund_id = str(int(refund_id))
                if isinstance(payment_id, Decimal):
                    if (
                        not payment_id.is_finite()
                        or payment_id <= 0
                        or payment_id > Decimal(2**63 - 1)
                        or payment_id != payment_id.to_integral_value()
                    ):
                        raise ValueError("cf_payment_id must be a positive integer")
                    payment_id = str(int(payment_id))
                if not isinstance(refund_id, str) or not refund_id:
                    raise ValueError("refund id is required")
                if payment_id is not None and not isinstance(payment_id, str):
                    raise ValueError("cf_payment_id must be a string")
                if refund.get("refund_currency", "INR") != "INR":
                    raise ValueError("only INR events are supported")
                processed_at = refund.get("processed_at")
                if processed_at is not None:
                    _timestamp(processed_at)
                outcome = (
                    "SUCCEEDED"
                    if status == "SUCCESS"
                    else "PENDING"
                    if status == "PENDING"
                    else "DECLINED"
                )
                return ParsedPspEvent(
                    self.provider,
                    event_id,
                    event_type,
                    payment_id,
                    refund_id,
                    amount,
                    "INR",
                    outcome,
                    None,
                    occurred_at,
                    data,
                )
            raise _error("PSP_EVENT_UNSUPPORTED", "Cashfree event type is unsupported")
        except RelayPayError:
            raise
        except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as error:
            raise _error(
                "INVALID_PSP_EVENT",
                "Cashfree webhook body is malformed",
                details={"reason": str(error)},
            ) from error

    def parse_settlement_report(self, csv_bytes: bytes) -> list[SettlementRow]:
        required = {
            "reference",
            "payment_id",
            "refund_id",
            "amount",
            "fee",
            "gst",
            "currency",
            "settled_at",
        }
        try:
            text = csv_bytes.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text), strict=True)
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ValueError("missing required settlement columns")
            rows: list[SettlementRow] = []
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as error:
                    raise _error(
                        "SETTLEMENT_ROW_INVALID",
                        "Cashfree settlement report is malformed",
                        details={"line": reader.reader.line_num, "reason": str(error)},
                    ) from error
                line = reader.line_num
                try:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("wrong number of columns")
                    if row["currency"] != "INR":
                        raise ValueError("only INR settlement rows are supported")
                    amount = _rupees_to_paise(row["amount"], "amount")
                    fee = _rupees_to_paise(row["fee"], "fee", allow_zero=True)
                    gst = _rupees_to_paise(row["gst"], "gst", allow_zero=True)
                    settled_at = _timestamp(row["settled_at"])
                    if not row["reference"] or (not row["payment_id"] and not row["refund_id"]):
                        raise ValueError("reference and a payment or refund id are required")
                    rows.append(
                        SettlementRow(
                            row["reference"],
                            row["payment_id"] or None,
                            row["refund_id"] or None,
                            amount,
                            fee,
                            gst,
                            "INR",
                            settled_at,
                        )
                    )
                except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                    raise _error(
                        "SETTLEMENT_ROW_INVALID",
                        "Cashfree settlement row is malformed",
                        details={"line": line, "reason": str(error)},
                    ) from error
            return rows
        except UnicodeDecodeError as error:
            raise _error(
                "SETTLEMENT_ROW_INVALID",
                "Cashfree settlement report is not UTF-8",
                details={"line": 1},
            ) from error
        except (csv.Error, ValueError) as error:
            raise _error(
                "SETTLEMENT_ROW_INVALID",
                "Cashfree settlement report is malformed",
                details={"line": 1, "reason": str(error)},
            ) from error
