import hashlib
import hmac

import httpx
import pytest
from relaypay_sdk import (
    APIKey,
    IdempotencyKey,
    RelayPayAPIError,
    RelayPayClient,
    retry_guidance,
    verify_webhook,
)


def test_api_key_exposes_environment_without_exposing_secret_parts() -> None:
    assert APIKey("rpk_test_abcdefgh.secret").environment == "TEST"
    assert APIKey("rpk_live_like_abcdefgh.secret").environment == "LIVE_LIKE"
    with pytest.raises(ValueError):
        APIKey("not-a-relaypay-key")
    assert IdempotencyKey.new().value.startswith("rpi_")
    with pytest.raises(ValueError):
        IdempotencyKey("")


def test_facade_follows_opaque_cursors_and_raises_typed_errors() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("after") == "cursor-1":
            return httpx.Response(
                200,
                json={"data": [], "nextCursor": None},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "pay_" + "a" * 32,
                        "merchantReference": "order-1",
                        "amount": 100,
                        "currency": "INR",
                        "authorizationStatus": None,
                        "captureStatus": None,
                        "createdAt": "2026-07-30T12:00:00+00:00",
                    }
                ],
                "nextCursor": "cursor-1",
            },
            request=request,
        )

    client = RelayPayClient(
        base_url="https://relaypay.test",
        api_key="rpk_test_abcdefgh.secret",
        transport=httpx.MockTransport(handler),
    )
    assert [item.id for item in client.iter_payment_intents(page_size=1)] == ["pay_" + "a" * 32]
    assert requests[1].url.params["after"] == "cursor-1"
    client.close()

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"code": "RATE_LIMITED", "message": "Slow down", "details": None}},
            headers={"Retry-After": "3", "X-Request-ID": "req_test"},
            request=request,
        )

    client = RelayPayClient(
        base_url="https://relaypay.test",
        api_key="rpk_test_abcdefgh.secret",
        transport=httpx.MockTransport(error_handler),
    )
    with pytest.raises(RelayPayAPIError) as raised:
        client.list_payment_intents()
    assert raised.value.code == "RATE_LIMITED"
    assert raised.value.retry_after == 3
    assert raised.value.request_id == "req_test"
    client.close()


def test_retry_guidance_requires_safe_or_idempotent_requests() -> None:
    assert not retry_guidance(method="POST", status_code=503).retryable
    assert retry_guidance(method="POST", status_code=503, idempotency_key="same-key").retryable
    assert retry_guidance(method="GET", status_code=429, retry_after=2).minimum_delay_seconds == 2


def test_webhook_verification_uses_exact_request_bytes() -> None:
    body = b'{"id":"evt_1","amount":100}'
    secret = "webhook-secret"
    timestamp = "1785412800"
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()

    assert verify_webhook(
        body=body,
        timestamp=timestamp,
        signature=f"v1={digest}",
        secret=secret,
    )
    assert not verify_webhook(
        body=body + b" ",
        timestamp=timestamp,
        signature=f"v1={digest}",
        secret=secret,
    )


def test_v2_commands_use_v2_paths_idempotency_keys_and_optional_bodies() -> None:
    seen: list[tuple[str, str, str | None, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get("Idempotency-Key"),
                request.content,
            )
        )
        return httpx.Response(202, json={"status": "PROCESSING"}, request=request)

    client = RelayPayClient(
        base_url="https://relaypay.test",
        api_key="rpk_test_abcdefgh.secret",
        transport=httpx.MockTransport(handler),
    )
    payment = "pay_" + "b" * 32
    client.authorize(payment, idempotency_key=IdempotencyKey("k1"), hold_seconds=3600)
    client.capture(payment, idempotency_key=IdempotencyKey("k2"), amount=60_000)
    client.capture(payment, idempotency_key=IdempotencyKey("k3"))
    client.void(payment, idempotency_key=IdempotencyKey("k4"))
    client.get_payment_intent_v2(payment)
    client.close()

    base = f"/api/v2/payment_intents/{payment}"
    assert seen == [
        ("POST", f"{base}/authorize", "k1", b'{"hold_seconds":3600}'),
        ("POST", f"{base}/capture", "k2", b'{"amount":60000}'),
        ("POST", f"{base}/capture", "k3", b"{}"),
        ("POST", f"{base}/void", "k4", b"{}"),
        ("GET", base, None, b""),
    ]
