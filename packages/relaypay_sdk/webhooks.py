import hashlib
import hmac


def verify_webhook(
    *,
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
) -> bool:
    """Verify RelayPay against the exact request bytes before JSON parsing."""
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return signature.startswith("v1=") and hmac.compare_digest(signature[3:], expected)
