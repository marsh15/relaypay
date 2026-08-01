from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from relaypay.config import Settings
from relaypay.errors import RelayPayError

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$")


def canonical_origin_request(
    *, method: str, target: str, timestamp: str, nonce: str, body_sha256: str, traceparent: str
) -> bytes:
    return "\n".join(
        ("relaypay-edge-v1", method.upper(), target, timestamp, nonce, body_sha256, traceparent)
    ).encode()


def sign_origin_request(
    *,
    secret: str,
    method: str,
    target: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
    traceparent: str,
) -> str:
    return hmac.new(
        secret.encode(),
        canonical_origin_request(
            method=method,
            target=target,
            timestamp=timestamp,
            nonce=nonce,
            body_sha256=body_sha256,
            traceparent=traceparent,
        ),
        hashlib.sha256,
    ).hexdigest()


def verify_origin_request(request: Request, body: bytes, settings: Settings) -> str:
    timestamp = request.headers.get("x-relaypay-edge-timestamp", "")
    nonce = request.headers.get("x-relaypay-edge-nonce", "")
    supplied_digest = request.headers.get("x-relaypay-edge-body-sha256", "")
    signature = request.headers.get("x-relaypay-edge-signature", "")
    traceparent = request.headers.get("traceparent", "")
    if request.headers.get("x-relaypay-edge-key-id") != "v1":
        raise _rejected()
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError as exc:
        raise _rejected() from exc
    if age > settings.EDGE_ORIGIN_REPLAY_SECONDS:
        raise _rejected()
    actual_digest = hashlib.sha256(body).hexdigest()
    if (
        not _NONCE.fullmatch(nonce)
        or not _HEX_64.fullmatch(supplied_digest)
        or not _HEX_64.fullmatch(signature)
        or not _TRACEPARENT.fullmatch(traceparent)
        or not hmac.compare_digest(actual_digest, supplied_digest)
    ):
        raise _rejected()
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    expected = sign_origin_request(
        secret=settings.EDGE_ORIGIN_SIGNING_SECRET.get_secret_value(),
        method=request.method,
        target=target,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=actual_digest,
        traceparent=traceparent,
    )
    if not hmac.compare_digest(signature, expected):
        raise _rejected()
    return traceparent


def _rejected() -> RelayPayError:
    return RelayPayError(
        code="EDGE_ORIGIN_SIGNATURE_INVALID",
        message="A valid edge origin signature is required",
        http_status=401,
    )


def install_edge_origin_boundary(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def edge_origin_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        guarded = request.url.path.startswith(("/api/v1", "/api/inbound/v1"))
        traceparent = request.headers.get("traceparent")
        if settings.EDGE_ORIGIN_SIGNATURE_REQUIRED and guarded:
            try:
                traceparent = verify_origin_request(request, await request.body(), settings)
            except RelayPayError as error:
                return JSONResponse(
                    status_code=error.http_status,
                    content={
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "details": None,
                        }
                    },
                )
        response = await call_next(request)
        if traceparent and _TRACEPARENT.fullmatch(traceparent):
            response.headers["X-RelayPay-Traceparent"] = traceparent
        return response
