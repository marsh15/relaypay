"""Build signed simulator-format PSP webhooks for tests (mirrors relaypay.psp.inbox)."""

import hashlib
import hmac
import time
import uuid
from typing import Any

from relaypay.idempotency import canonical_json_bytes

WEBHOOK_SECRET = "dev-provider-webhook-secret-change-me"


def signature_for(body: bytes, timestamp: str, *, secret: str = WEBHOOK_SECRET) -> str:
    """The ``X-Provider-Signature`` value for ``body`` signed at ``timestamp``."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"v1={digest.hexdigest()}"


def signed_event(
    effect: dict[str, Any],
    *,
    event_id: str | None = None,
    secret: str = WEBHOOK_SECRET,
    timestamp: int | None = None,
    occurred_at: str = "2026-10-21T00:00:00+00:00",
) -> tuple[dict[str, str], bytes]:
    """Return ``(headers, body)`` for one ``effect.updated`` event."""
    identifier = event_id or f"evt_{uuid.uuid4().hex}"
    body = canonical_json_bytes(
        {
            "effect": effect,
            "eventId": identifier,
            "eventType": "effect.updated",
            "occurredAt": occurred_at,
        }
    )
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    headers = {
        "X-Provider-Event-Id": identifier,
        "X-Provider-Timestamp": stamp,
        "X-Provider-Signature": signature_for(body, stamp, secret=secret),
        "Content-Type": "application/json",
    }
    return headers, body
