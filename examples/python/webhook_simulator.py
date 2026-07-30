import argparse
import hashlib
import hmac
import json
import time
import uuid

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one signed synthetic RelayPay webhook")
    parser.add_argument("--url", default="http://localhost:8002/webhooks/relaypay")
    parser.add_argument("--secret", default="dev-receiver-webhook-secret")
    args = parser.parse_args()

    event_id = f"evt_{uuid.uuid4().hex}"
    body = json.dumps(
        {"id": event_id, "type": "payment.captured", "synthetic": True},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(
        args.secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    response = httpx.post(
        args.url,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-RelayPay-Event-Id": event_id,
            "X-RelayPay-Timestamp": timestamp,
            "X-RelayPay-Signature": f"v1={digest}",
        },
        timeout=5,
    )
    print(response.status_code, response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
