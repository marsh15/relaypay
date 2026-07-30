import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class CursorPosition:
    created_at: datetime
    identifier: str


def filter_fingerprint(filters: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(filters)).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def invalid_cursor() -> RelayPayError:
    return RelayPayError(
        code="INVALID_CURSOR",
        message="The pagination cursor is invalid or incompatible with these filters",
        http_status=400,
    )


def encode_cursor(
    position: CursorPosition,
    *,
    filters: dict[str, object],
    secret: str,
) -> str:
    payload = canonical_json_bytes(
        {
            "v": 1,
            "createdAt": position.created_at.isoformat(),
            "id": position.identifier,
            "filters": filter_fingerprint(filters),
        }
    )
    signature = hmac.digest(secret.encode(), payload, "sha256")
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def decode_cursor(
    token: str,
    *,
    filters: dict[str, object],
    secret: str,
) -> CursorPosition:
    try:
        payload_token, signature_token = token.split(".", 1)
        payload = _b64decode(payload_token)
        signature = _b64decode(signature_token)
        expected = hmac.digest(secret.encode(), payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise invalid_cursor()
        document: Any = json.loads(payload)
        if (
            not isinstance(document, dict)
            or document.get("v") != 1
            or document.get("filters") != filter_fingerprint(filters)
            or not isinstance(document.get("createdAt"), str)
            or not isinstance(document.get("id"), str)
        ):
            raise invalid_cursor()
        return CursorPosition(
            created_at=datetime.fromisoformat(document["createdAt"]),
            identifier=document["id"],
        )
    except RelayPayError:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise invalid_cursor() from error
