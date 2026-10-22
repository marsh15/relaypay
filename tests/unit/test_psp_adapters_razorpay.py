"""Razorpay adapter contract and fixture coverage."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from relaypay.errors import RelayPayError
from relaypay.psp.adapters.razorpay import RazorpayAdapter

FIXTURES = Path(__file__).parents[1] / "fixtures" / "psp" / "razorpay"
SECRET = "rzp_test_fake_webhook_secret"
NOW = datetime.fromtimestamp(1_782_000_001, tz=UTC)
EVENTS = {
    "payment.authorized": ("SUCCEEDED", "pay_TEST00000001", None),
    "payment.captured": ("SUCCEEDED", "pay_TEST00000002", None),
    "payment.failed": ("DECLINED", "pay_TEST00000003", None),
    "refund.processed": ("SUCCEEDED", "pay_TEST00000004", "rfnd_TEST00000001"),
    "refund.failed": ("DECLINED", "pay_TEST00000005", "rfnd_TEST00000002"),
}


def _body(event_type: str) -> bytes:
    return (FIXTURES / f"{event_type}.json").read_bytes()


def _headers(body: bytes, secret: str = SECRET) -> dict[str, str]:
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_TEST00000001"}


def test_valid_signature_verifies() -> None:
    body = _body("payment.captured")
    RazorpayAdapter().verify(_headers(body), body, SECRET, NOW)


@pytest.mark.parametrize(
    ("body_change", "secret", "expected_code"),
    [(b" ", SECRET, "INVALID_WEBHOOK_SIGNATURE"), (b"", "wrong", "INVALID_WEBHOOK_SIGNATURE")],
)
def test_invalid_signatures_share_signature_error(
    body_change: bytes, secret: str, expected_code: str
) -> None:
    body = _body("payment.captured")
    headers = _headers(body)
    altered = body + body_change if body_change else body
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().verify(headers, altered, secret, NOW)
    assert raised.value.code == expected_code


def test_missing_required_header_has_specific_error() -> None:
    body = _body("payment.captured")
    signature = _headers(body)["X-Razorpay-Signature"]
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().verify({"X-Razorpay-Signature": signature}, body, SECRET, NOW)
    assert raised.value.code == "PSP_WEBHOOK_HEADERS_MISSING"


def test_non_ascii_signature_has_signature_error() -> None:
    body = _body("payment.captured")
    headers = _headers(body)
    headers["X-Razorpay-Signature"] = "☃"
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().verify(headers, body, SECRET, NOW)
    assert raised.value.code == "INVALID_WEBHOOK_SIGNATURE"


def test_signed_stale_event_has_replay_error() -> None:
    payload = json.loads(_body("payment.captured"))
    payload["created_at"] = 1_782_000_000 - 301
    body = json.dumps(payload, separators=(",", ":")).encode()
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().verify(_headers(body), body, SECRET, NOW)
    assert raised.value.code == "WEBHOOK_REPLAY_WINDOW_EXCEEDED"


@pytest.mark.parametrize("event_type", EVENTS)
def test_documented_event_normalizes(event_type: str) -> None:
    parsed = RazorpayAdapter().parse_event(
        _body(event_type), headers={"X-Razorpay-Event-Id": "evt_TEST00000001"}
    )
    outcome, payment_id, refund_id = EVENTS[event_type]
    assert parsed.provider == "RAZORPAY"
    assert parsed.event_id == "evt_TEST00000001"
    assert parsed.event_type == event_type
    assert parsed.outcome == outcome
    assert parsed.payment_id == payment_id
    assert parsed.refund_id == refund_id
    assert parsed.currency == "INR"
    assert parsed.occurred_at == datetime.fromtimestamp(1_782_000_001, tz=UTC)


def test_missing_event_header_is_not_fabricated() -> None:
    parsed = RazorpayAdapter().parse_event(_body("payment.captured"))
    assert parsed.event_id is None


@pytest.mark.parametrize("bad_amount", [True, -1, 1.25, "500"])
def test_rejects_boolean_negative_or_non_integer_amount(bad_amount: Any) -> None:
    payload = json.loads(_body("payment.captured"))
    payload["payload"]["payment"]["entity"]["amount"] = bad_amount
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().parse_event(json.dumps(payload).encode())
    assert raised.value.code == "INVALID_PSP_EVENT"


def test_rejects_malformed_and_unsupported_events() -> None:
    with pytest.raises(RelayPayError) as malformed:
        RazorpayAdapter().parse_event(b"{")
    assert malformed.value.code == "INVALID_PSP_EVENT"
    unsupported = json.loads(_body("payment.captured"))
    unsupported["event"] = "payment.pending"
    with pytest.raises(RelayPayError) as not_supported:
        RazorpayAdapter().parse_event(json.dumps(unsupported).encode())
    assert not_supported.value.code == "PSP_EVENT_UNSUPPORTED"


def test_settlement_csv_parses_fees_and_tax_as_paise() -> None:
    rows = RazorpayAdapter().parse_settlement_report((FIXTURES / "settlement.csv").read_bytes())
    assert len(rows) == 2
    assert (rows[0].amount, rows[0].fee, rows[0].gst) == (3499, 103, 19)
    assert (rows[1].payment_id, rows[1].refund_id) == (None, "rfnd_TEST00000001")


@pytest.mark.parametrize(
    "contents",
    [
        b"reference,payment_id,refund_id,amount,fee,tax,currency,settled_at\n"
        b"setl_1,pay_1,,12.50,1,0,INR,2026-06-21T12:00:00Z\n",
        b"reference,payment_id,refund_id,amount,fee,tax,currency,settled_at\n"
        b"setl_1,pay_1,,12,-1,0,INR,2026-06-21T12:00:00Z\n",
        b"reference,payment_id,refund_id,amount,fee,tax,currency,settled_at\n"
        b"setl_1,pay_1,,12,1,0,INR,not-a-date\n",
        b"reference,payment_id,refund_id,amount,fee,tax,currency,settled_at\nshort,row\n",
    ],
)
def test_malformed_settlement_row_has_line_number(contents: bytes) -> None:
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().parse_settlement_report(contents)
    assert raised.value.code == "SETTLEMENT_ROW_INVALID"
    assert raised.value.details["line"] == 2


def test_signed_invalid_timestamp_has_specific_code() -> None:
    payload = json.loads(_body("payment.captured"))
    payload["created_at"] = True
    body = json.dumps(payload).encode()
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().verify(_headers(body), body, SECRET, NOW)
    assert raised.value.code == "INVALID_WEBHOOK_TIMESTAMP"


def test_unrepresentable_timestamp_has_provider_error_codes() -> None:
    payload = json.loads(_body("payment.captured"))
    payload["created_at"] = 10**100
    body = json.dumps(payload).encode()
    with pytest.raises(RelayPayError) as verify_error:
        RazorpayAdapter().verify(_headers(body), body, SECRET, NOW)
    assert verify_error.value.code == "INVALID_WEBHOOK_TIMESTAMP"
    assert verify_error.value.http_status == 401

    with pytest.raises(RelayPayError) as parse_error:
        RazorpayAdapter().parse_event(body)
    assert parse_error.value.code == "INVALID_WEBHOOK_TIMESTAMP"
    assert parse_error.value.http_status == 400


def test_csv_syntax_error_uses_actual_reader_line() -> None:
    contents = (
        b"reference,payment_id,refund_id,amount,fee,tax,currency,settled_at\n"
        b"setl_1,pay_1,,12,1,0,INR,2026-06-21T12:00:00Z\n"
        b'"unterminated\n'
    )
    with pytest.raises(RelayPayError) as raised:
        RazorpayAdapter().parse_settlement_report(contents)
    assert raised.value.code == "SETTLEMENT_ROW_INVALID"
    assert raised.value.details["line"] == 3


def test_occurred_at_uses_envelope_timestamp_not_entity_creation_time() -> None:
    payload = json.loads(_body("payment.captured"))
    payload["created_at"] = 1_782_000_015
    payload["payload"]["payment"]["entity"]["created_at"] = 1_782_000_000
    parsed = RazorpayAdapter().parse_event(json.dumps(payload).encode())
    assert parsed.occurred_at == datetime.fromtimestamp(1_782_000_015, tz=UTC)
