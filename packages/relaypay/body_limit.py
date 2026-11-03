"""Request-body cap for the Python HTTP services, enforced before anything reads the body.

The middleware is plain ASGI, so it runs outside FastAPI's body parsing, signature checks and
form parsing: nothing downstream sees more than ``max_bytes``. A declared Content-Length over the
cap is refused without calling ``receive``. Otherwise the body is read in blocks until it ends or
crosses the cap, then replayed byte-for-byte to the application. Memory is bounded by the cap plus
one transport chunk, and the replay list is bounded by the cap divided by the block size.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

from fastapi import FastAPI

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

DEFAULT_MAX_BODY_BYTES: Final = 2 * 1024 * 1024
REQUEST_BODY_TOO_LARGE: Final = "REQUEST_BODY_TOO_LARGE"
REQUEST_BODY_INCOMPLETE: Final = "REQUEST_BODY_INCOMPLETE"

_TOO_LARGE_MESSAGE: Final = "Request body exceeds the size limit"
_INCOMPLETE_MESSAGE: Final = "The connection closed before the request body completed"
# Blocks are sealed at this size so a client that sends one byte per chunk cannot make the
# replay list grow without bound.
_BLOCK_BYTES: Final = 64 * 1024
# A Content-Length with more digits than this cannot be a real body; it is treated as oversized.
_MAX_DECLARED_DIGITS: Final = 18
_SECURITY_HEADERS: Final[tuple[tuple[bytes, bytes], ...]] = (
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
)


class _Overflow(Exception):
    """The body crossed the cap, so reading stopped."""


class _Disconnected(Exception):
    """The client went away before the body was complete."""


class BodyLimitMiddleware:
    """Refuse HTTP request bodies over ``max_bytes``; pass every other scope through unchanged."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        if max_bytes < 0:
            raise ValueError("max_bytes must not be negative")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _send_error(
                scope, send, 413, REQUEST_BODY_TOO_LARGE, _TOO_LARGE_MESSAGE, self.max_bytes
            )
            return
        try:
            blocks = await _read_bounded(receive, self.max_bytes)
        except _Overflow:
            await _send_error(
                scope, send, 413, REQUEST_BODY_TOO_LARGE, _TOO_LARGE_MESSAGE, self.max_bytes
            )
            return
        except _Disconnected:
            await _send_error(scope, send, 400, REQUEST_BODY_INCOMPLETE, _INCOMPLETE_MESSAGE, None)
            return
        await self.app(scope, _replay(blocks, receive), send)


def install_request_body_limit(app: FastAPI, *, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
    """Wrap ``app`` in the body cap.

    Starlette runs the most recently added middleware first. Call this after
    ``install_edge_origin_boundary`` so the cap runs before the origin signature reads the body.
    """
    app.add_middleware(BodyLimitMiddleware, max_bytes=max_bytes)


def _declared_length(scope: Scope) -> int | None:
    values = [
        value for name, value in scope.get("headers", ()) if name.lower() == b"content-length"
    ]
    if len(values) != 1 or not values[0].isdigit():
        return None
    digits = values[0].lstrip(b"0") or b"0"
    if len(digits) > _MAX_DECLARED_DIGITS:
        return 10**_MAX_DECLARED_DIGITS
    return int(digits)


async def _read_bounded(receive: Receive, max_bytes: int) -> list[bytes]:
    """Read the body into blocks, stopping at the first byte past ``max_bytes``."""
    sealed: list[bytes] = []
    pending = bytearray()
    total = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            raise _Disconnected
        chunk: bytes = message.get("body", b"")
        total += len(chunk)
        if total > max_bytes:
            raise _Overflow
        pending.extend(chunk)
        if len(pending) >= _BLOCK_BYTES:
            sealed.append(bytes(pending))
            pending = bytearray()
        if not message.get("more_body", False):
            break
    if pending or not sealed:
        sealed.append(bytes(pending))
    return sealed


def _replay(blocks: list[bytes], receive: Receive) -> Receive:
    """Serve the buffered blocks, then hand over to the original ``receive``."""
    queue: deque[bytes] = deque(blocks)

    async def replay() -> Message:
        if queue:
            body = queue.popleft()
            return {"type": "http.request", "body": body, "more_body": bool(queue)}
        return await receive()

    return replay


async def _send_error(
    scope: Scope,
    send: Send,
    status: int,
    code: str,
    message: str,
    max_bytes: int | None,
) -> None:
    details = None if max_bytes is None else {"maxBytes": max_bytes}
    payload = json.dumps(
        {"error": {"code": code, "message": message, "details": details}},
        separators=(",", ":"),
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                # HTTP/2 forbids connection-specific headers.
                *(
                    [(b"connection", b"close")]
                    if scope.get("http_version", "1.1").startswith("1.")
                    else []
                ),
                (b"x-request-id", _request_id(scope)),
                *_SECURITY_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload, "more_body": False})


def _request_id(scope: Scope) -> bytes:
    for name, value in scope.get("headers", ()):
        if name.lower() == b"x-request-id" and re.fullmatch(rb"[A-Za-z0-9_-]{1,96}", value):
            return bytes(value)
    return f"req_{uuid.uuid4().hex}".encode()


def safe_request_id(value: str | None) -> str:
    """Retain a bounded tracing token without reflecting arbitrary header text."""
    if value is not None and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        return value
    return f"req_{uuid.uuid4().hex}"
