import hashlib
import hmac

from relaypay_sdk import verify_webhook


def main() -> None:
    body = b'{"id":"evt_synthetic","type":"payment.captured"}'
    timestamp = "1785412800"
    secret = "synthetic-webhook-secret"  # noqa: S105 - documented synthetic fixture
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify_webhook(
        body=body,
        timestamp=timestamp,
        signature=f"v1={digest}",
        secret=secret,
    )
    print("Exact-byte webhook verification: PASS")


if __name__ == "__main__":
    main()
