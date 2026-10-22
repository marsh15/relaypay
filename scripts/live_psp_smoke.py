"""Optional read-only sandbox authentication checks; never run in CI.

Set RAZORPAY_TEST_KEY_ID/RAZORPAY_TEST_KEY_SECRET or
CASHFREE_TEST_CLIENT_ID/CASHFREE_TEST_CLIENT_SECRET explicitly in the environment.
Cashfree additionally needs CASHFREE_TEST_ORDER_ID for an existing sandbox order.
This does not verify webhook fixture shapes: retain a captured sandbox delivery
before promoting either adapter to inbox use. No payment or refund is created.
"""

import os
import re
from collections.abc import Mapping

import httpx


def smoke(environ: Mapping[str, str], client: httpx.Client) -> list[str]:
    if environ.get("CI", "").lower() not in {"", "0", "false"}:
        return ["SKIP: live PSP smoke is disabled in CI"]
    results: list[str] = []
    razorpay_id = environ.get("RAZORPAY_TEST_KEY_ID")
    razorpay_secret = environ.get("RAZORPAY_TEST_KEY_SECRET")
    if not razorpay_id or not razorpay_secret:
        results.append("SKIP Razorpay: test keys are not configured")
    elif not razorpay_id.startswith("rzp_test_"):
        raise ValueError("Razorpay smoke requires a test-mode key id")
    else:
        response = client.get(
            "https://api.razorpay.com/v1/payments",
            params={"count": 1},
            auth=httpx.BasicAuth(razorpay_id, razorpay_secret),
        )
        if response.status_code != 200:
            raise RuntimeError(f"Razorpay sandbox read failed: HTTP {response.status_code}")
        results.append("PASS Razorpay: read-only test-mode payments lookup")

    cashfree_id = environ.get("CASHFREE_TEST_CLIENT_ID")
    cashfree_secret = environ.get("CASHFREE_TEST_CLIENT_SECRET")
    order_id = environ.get("CASHFREE_TEST_ORDER_ID")
    if not cashfree_id or not cashfree_secret or not order_id:
        results.append("SKIP Cashfree: test keys and an existing sandbox order id are required")
    elif re.fullmatch(r"[A-Za-z0-9_-]+", order_id) is None:
        raise ValueError("Cashfree sandbox order id contains invalid characters")
    else:
        response = client.get(
            f"https://sandbox.cashfree.com/pg/orders/{order_id}",
            headers={
                "x-client-id": cashfree_id,
                "x-client-secret": cashfree_secret,
                "x-api-version": "2025-01-01",
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Cashfree sandbox read failed: HTTP {response.status_code}")
        results.append("PASS Cashfree: read-only sandbox order lookup")
    return results


def main() -> int:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            results = smoke(os.environ, client)
    except (ValueError, RuntimeError, httpx.HTTPError) as error:
        # Never print the response body, auth headers, request URL or credentials.
        print(f"Live PSP smoke failed ({type(error).__name__})")
        return 1
    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
