"""Explicit-only smoke coverage, including offline guards and optional sandbox reads."""

import os

import httpx
import pytest

from scripts.live_psp_smoke import smoke

pytestmark = pytest.mark.live_psp


def test_absent_keys_skip_without_http() -> None:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert all(result.startswith("SKIP") for result in smoke({}, client))
    assert requests == []


def test_ci_guard_ignores_even_configured_keys() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"CI attempted network access to {request.url.host}")

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert smoke({"CI": "true", "RAZORPAY_TEST_KEY_ID": "rzp_test_fake"}, client) == [
            "SKIP: live PSP smoke is disabled in CI"
        ]


def test_only_read_only_provider_endpoints_are_called() -> None:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    environ = {
        "RAZORPAY_TEST_KEY_ID": "rzp_test_obviously_fake",
        "RAZORPAY_TEST_KEY_SECRET": "obviously-fake-razorpay-secret",
        "CASHFREE_TEST_CLIENT_ID": "obviously-fake-cashfree-id",
        "CASHFREE_TEST_CLIENT_SECRET": "obviously-fake-cashfree-secret",
        "CASHFREE_TEST_ORDER_ID": "order_fake_123",
    }
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert all(result.startswith("PASS") for result in smoke(environ, client))
    assert [(request.method, request.url.host, request.url.path) for request in requests] == [
        ("GET", "api.razorpay.com", "/v1/payments"),
        ("GET", "sandbox.cashfree.com", "/pg/orders/order_fake_123"),
    ]
    assert requests[1].headers["x-api-version"] == "2025-01-01"


def test_razorpay_live_key_is_rejected_without_http() -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client,
        pytest.raises(ValueError, match="test-mode"),
    ):
        smoke(
            {
                "RAZORPAY_TEST_KEY_ID": "rzp_live_obviously_fake",
                "RAZORPAY_TEST_KEY_SECRET": "obviously-fake-secret",
            },
            client,
        )


def test_sandbox_with_explicit_environment_keys() -> None:
    if os.environ.get("CI"):
        pytest.skip("live PSP checks are never run in CI")
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        results = smoke(os.environ, client)
    if all(result.startswith("SKIP") for result in results):
        pytest.skip("provider test keys are absent")
    assert any(result.startswith("PASS") for result in results)
