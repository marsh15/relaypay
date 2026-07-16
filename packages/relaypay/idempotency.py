import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class Fingerprint:
    canonical_bytes: bytes
    sha256: bytes
    safe_summary: dict[str, Any]


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_fingerprint(
    *,
    api_version: str,
    method: str,
    route_template: str,
    path_params: Mapping[str, str],
    body: BaseModel,
) -> Fingerprint:
    summary: dict[str, Any] = {
        "api_version": api_version,
        "method": method.upper(),
        "route_template": route_template,
        "path_params": dict(path_params),
        "body": body.model_dump(mode="json", exclude_none=False),
    }
    canonical = canonical_json_bytes(summary)
    return Fingerprint(
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).digest(),
        safe_summary=summary,
    )


def digest_secret(value: str, pepper: str) -> bytes:
    return hmac.new(pepper.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()


def key_hint(value: str) -> str:
    if len(value) <= 8:
        return "redacted"
    return f"…{value[-6:]}"
