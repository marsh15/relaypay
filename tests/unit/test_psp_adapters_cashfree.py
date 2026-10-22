import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from relaypay.errors import RelayPayError
from relaypay.psp.adapters.cashfree import CashfreeAdapter

FIXTURES = Path(__file__).parents[1] / "fixtures" / "psp" / "cashfree"
SECRET = "fake-cashfree-webhook-secret"
NOW = datetime(2025, 1, 15, 6, 16, 10, tzinfo=UTC)


def signed_headers(body: bytes, *, timestamp: str = "1736921770000") -> dict[str, str]:
    signature = base64.b64encode(
        hmac.new(SECRET.encode(), timestamp.encode() + body, hashlib.sha256).digest()
    ).decode()
    return {"x-webhook-timestamp": timestamp, "x-webhook-signature": signature}


def error_code(call) -> str:
    with pytest.raises(RelayPayError) as caught:
        call()
    return caught.value.code


def test_valid_signature_and_header_event_identity() -> None:
    body = (FIXTURES / "payment_success.json").read_bytes()
    headers = signed_headers(body) | {"x-idempotency-key": "cashfree-event-abc"}
    CashfreeAdapter().verify(headers, body, SECRET, NOW)
    event = CashfreeAdapter().parse_event(body, headers=headers)
    assert event.event_id == "cashfree-event-abc"
    assert event.payment_id == "1453002795"
    assert event.amount == 100
    assert event.currency == "INR"
    assert event.outcome == "SUCCEEDED"
    assert event.occurred_at == datetime(2025, 1, 15, 5, 46, 10, tzinfo=UTC)
    assert event.metadata["order"]["order_id"] == "order_OFR_2"


@pytest.mark.parametrize(
    ("headers", "body", "secret", "now", "code"),
    [
        ({}, b"{}", SECRET, NOW, "PSP_WEBHOOK_HEADERS_MISSING"),
        (signed_headers(b"original"), b"tampered", SECRET, NOW, "INVALID_WEBHOOK_SIGNATURE"),
        (signed_headers(b"{}"), b"{}", "wrong-secret", NOW, "INVALID_WEBHOOK_SIGNATURE"),
        (
            signed_headers(b"{}", timestamp="1736921170000"),
            b"{}",
            SECRET,
            NOW,
            "WEBHOOK_REPLAY_WINDOW_EXCEEDED",
        ),
        (
            {"x-webhook-timestamp": "not-a-timestamp", "x-webhook-signature": "x"},
            b"{}",
            SECRET,
            NOW,
            "INVALID_WEBHOOK_TIMESTAMP",
        ),
        (
            {"x-webhook-timestamp": "999999999999999999999999999999", "x-webhook-signature": "x"},
            b"{}",
            SECRET,
            NOW,
            "INVALID_WEBHOOK_TIMESTAMP",
        ),
        (
            {"x-webhook-timestamp": "1736921770000", "x-webhook-signature": "é"},
            b"{}",
            SECRET,
            NOW,
            "INVALID_WEBHOOK_SIGNATURE",
        ),
    ],
)
def test_signature_failures(headers, body, secret, now, code) -> None:
    assert error_code(lambda: CashfreeAdapter().verify(headers, body, secret, now)) == code


@pytest.mark.parametrize(
    ("fixture", "event_type", "outcome", "paise"),
    [
        ("payment_success.json", "PAYMENT_SUCCESS_WEBHOOK", "SUCCEEDED", 100),
        ("payment_failed.json", "PAYMENT_FAILED_WEBHOOK", "DECLINED", 180),
        ("payment_user_dropped.json", "PAYMENT_USER_DROPPED_WEBHOOK", "DECLINED", 200),
    ],
)
def test_payment_events(fixture: str, event_type: str, outcome: str, paise: int) -> None:
    event = CashfreeAdapter().parse_event((FIXTURES / fixture).read_bytes())
    assert (event.event_type, event.outcome, event.amount) == (event_type, outcome, paise)
    assert event.payment_id is not None
    assert event.refund_id is None
    assert event.occurred_at.tzinfo is UTC
    expected_decline = "GATEWAY_ERROR" if fixture == "payment_failed.json" else None
    assert event.decline_code == expected_decline


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        ("SUCCESS", "SUCCEEDED"),
        ("FAILED", "DECLINED"),
        ("CANCELLED", "DECLINED"),
        ("PENDING", "PENDING"),
    ],
)
def test_refund_status_events(status: str, outcome: str) -> None:
    body = json.dumps(
        {
            "data": {
                "refund": {
                    "cf_refund_id": "refund_123",
                    "cf_payment_id": "payment_123",
                    "refund_amount": 12.34,
                    "refund_status": status,
                    "processed_at": "2025-01-15T12:20:29+05:30",
                }
            },
            "event_time": "2025-01-15T12:20:30+05:30",
            "type": "REFUND_STATUS_WEBHOOK",
        }
    ).encode()
    event = CashfreeAdapter().parse_event(body)
    assert event.event_type == "REFUND_STATUS_WEBHOOK"
    assert event.refund_id == "refund_123"
    assert event.amount == 1234
    assert event.outcome == outcome
    assert event.occurred_at == datetime(2025, 1, 15, 6, 50, 30, tzinfo=UTC)
    assert event.metadata["refund"]["refund_status"] == status


def test_documented_refund_webhook_accepts_numeric_provider_ids() -> None:
    event = CashfreeAdapter().parse_event((FIXTURES / "refund_success.json").read_bytes())
    assert event.event_id is None
    assert event.payment_id == "789727431"
    assert event.refund_id == "11325632"
    assert event.amount == 200
    assert event.outcome == "SUCCEEDED"
    assert event.occurred_at == datetime(2022, 2, 28, 7, 34, 28, tzinfo=UTC)


def test_documented_auto_refund_webhook() -> None:
    event = CashfreeAdapter().parse_event((FIXTURES / "auto_refund_success.json").read_bytes())
    assert event.event_type == "AUTO_REFUND_STATUS_WEBHOOK"
    assert event.payment_id == "2148333968"
    assert event.refund_id == "1243460973"
    assert event.amount == 3900


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{}}',
        b'{"type":"UNKNOWN","event_time":"2025-01-15T00:00:00Z","data":{}}',
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{"order":{"order_currency":"INR"},"payment":{"payment_status":"FAILED","payment_amount":1,"payment_currency":"INR","payment_time":"2025-01-15T00:00:00Z","cf_payment_id":"p"}}}',
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{"order":{"order_currency":"INR"},"payment":{"payment_status":"SUCCESS","payment_amount":true,"payment_currency":"INR","payment_time":"2025-01-15T00:00:00Z","cf_payment_id":"p"}}}',
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{"order":{"order_currency":"INR"},"payment":{"payment_status":"SUCCESS","payment_amount":1.001,"payment_currency":"INR","payment_time":"2025-01-15T00:00:00Z","cf_payment_id":"p"}}}',
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{"order":{"order_currency":"INR"},"payment":{"payment_status":"SUCCESS","payment_amount":1e999999,"payment_currency":"INR","payment_time":"2025-01-15T00:00:00Z","cf_payment_id":"p"}}}',
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{"order":{"order_currency":"INR"},"payment":{"payment_status":"SUCCESS","payment_amount":"100.0000000000000000000000000001","payment_currency":"INR","payment_time":"2025-01-15T00:00:00Z","cf_payment_id":"p"}}}',
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{"order":{"order_currency":"INR"},"payment":{"payment_status":"SUCCESS","payment_amount":"1e-9999999","payment_currency":"INR","payment_time":"2025-01-15T00:00:00Z","cf_payment_id":"p"}}}',
        b'{"type":"PAYMENT_SUCCESS_WEBHOOK","event_time":"2025-01-15T00:00:00Z","data":{"order":{"order_currency":"INR"},"payment":{"payment_status":"SUCCESS","payment_amount":1,"payment_currency":"INR","payment_time":"0001-01-01T00:00:00+23:59","cf_payment_id":"p"}}}',
    ],
)
def test_malformed_and_unsupported_events(body: bytes) -> None:
    code = error_code(lambda: CashfreeAdapter().parse_event(body))
    expected = "PSP_EVENT_UNSUPPORTED" if b'"UNKNOWN"' in body else "INVALID_PSP_EVENT"
    assert code == expected


def test_settlement_csv_converts_amounts_to_paise() -> None:
    rows = CashfreeAdapter().parse_settlement_report((FIXTURES / "settlement.csv").read_bytes())
    assert len(rows) == 1
    assert (rows[0].amount, rows[0].fee, rows[0].gst) == (100, 2, 0)
    assert rows[0].settled_at.tzinfo is UTC


@pytest.mark.parametrize(
    "csv_bytes",
    [
        b"reference,payment_id,refund_id,amount,fee,gst,currency,settled_at\nr1,p1,,1.001,0.01,0.00,INR,2025-01-15T12:20:29+05:30\n",
        b"reference,payment_id,refund_id,amount,fee,gst,currency,settled_at\nr1,p1,,true,0.01,0.00,INR,2025-01-15T12:20:29+05:30\n",
        b"reference,payment_id,refund_id,amount,fee,gst,currency,settled_at\nr1,p1,,1.00,0.01,0.00,USD,2025-01-15T12:20:29+05:30\n",
        b"reference,payment_id,refund_id,amount,fee,gst,currency,settled_at\nr1,p1,,100.0000000000000000000000000001,0.01,0.00,INR,2025-01-15T12:20:29+05:30\n",
        b"reference,payment_id,refund_id,amount,fee,gst,currency,settled_at\nr1,p1,,1e-9999999,0.01,0.00,INR,2025-01-15T12:20:29+05:30\n",
        b"reference,payment_id,refund_id,amount,fee,gst,currency,settled_at\nr1,p1,,1.00,0.01,0.00,INR,0001-01-01T00:00:00+23:59\n",
    ],
)
def test_malformed_settlement_rows_include_line_number(csv_bytes: bytes) -> None:
    with pytest.raises(RelayPayError) as caught:
        CashfreeAdapter().parse_settlement_report(csv_bytes)
    assert caught.value.code == "SETTLEMENT_ROW_INVALID"
    assert caught.value.details["line"] == 2


def test_csv_syntax_error_reports_reader_line_number() -> None:
    csv_bytes = (
        b"reference,payment_id,refund_id,amount,fee,gst,currency,settled_at\n"
        b"r1,p1,,1.00,0.01,0.00,INR,2025-01-15T12:20:29+05:30\n"
        b"r2,p2,,1.00,0.01,0.00,INR,2025-01-15T12:20:29+05:30\n"
        b'r3,p3,,"1.00x,0.01,0.00,INR,2025-01-15T12:20:29+05:30\n'
    )
    with pytest.raises(RelayPayError) as caught:
        CashfreeAdapter().parse_settlement_report(csv_bytes)
    assert caught.value.code == "SETTLEMENT_ROW_INVALID"
    assert caught.value.details["line"] == 4
