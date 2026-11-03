"""Request-body cap: raw ASGI fixtures, then FastAPI ordering and service checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from relaypay.body_limit import (
    REQUEST_BODY_INCOMPLETE,
    REQUEST_BODY_TOO_LARGE,
    BodyLimitMiddleware,
    install_request_body_limit,
    safe_request_id,
)
from relaypay.config import Settings
from relaypay.disputes.network import DeterministicDisputeNetwork
from relaypay.disputes.package import MAX_PACKAGE_BYTES
from relaypay.edge.origin import install_edge_origin_boundary, sign_origin_request

from apps.dispute_network.main import create_app as create_dispute_app

LIMIT = 16
Message = MutableMapping[str, Any]


class Receive:
    """Raw ASGI receive: serves queued messages, then reports a disconnect."""

    def __init__(self, *messages: Message) -> None:
        self._queue: deque[Message] = deque(messages)
        self.calls = 0

    async def __call__(self) -> Message:
        self.calls += 1
        if self._queue:
            return self._queue.popleft()
        return {"type": "http.disconnect"}


class Send:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)


class Downstream:
    """Stands in for the application: reads the body it is given and records what it saw."""

    def __init__(self) -> None:
        self.calls = 0
        self.pieces: list[bytes] = []
        self.receive_seen: object = None

    @property
    def body(self) -> bytes:
        return b"".join(self.pieces)

    async def __call__(
        self, scope: Message, receive: Callable[[], Awaitable[Message]], send: Send
    ) -> None:
        self.calls += 1
        self.receive_seen = receive
        if scope["type"] != "http":
            return
        while True:
            message = await receive()
            self.pieces.append(message["body"])
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": self.body})


def _messages(chunks: Iterable[bytes]) -> list[Message]:
    parts = list(chunks)
    return [
        {"type": "http.request", "body": part, "more_body": index < len(parts) - 1}
        for index, part in enumerate(parts)
    ]


def _http_scope(*headers: tuple[bytes, bytes]) -> Message:
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/proof",
        "headers": list(headers),
        "query_string": b"",
    }


def _run(
    app: Callable[[Message, Callable[[], Awaitable[Message]], Send], Awaitable[None]],
    scope: Message,
    receive: Callable[[], Awaitable[Message]],
) -> Send:
    send = Send()
    asyncio.run(app(scope, receive, send))
    return send


def _headers(send: Send) -> dict[str, str]:
    start = send.messages[0]
    return {name.decode(): value.decode() for name, value in start["headers"]}


def _assert_refusal_headers(send: Send) -> None:
    headers = _headers(send)
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["connection"] == "close"


def test_declared_length_over_cap_is_refused_before_receive() -> None:
    downstream = Downstream()
    receive = Receive(*_messages([b"SECRET-PAYLOAD"]))
    scope = _http_scope((b"content-length", b"17"), (b"x-request-id", b"req-123"))
    send = _run(BodyLimitMiddleware(downstream, max_bytes=LIMIT), scope, receive)
    assert receive.calls == 0
    assert downstream.calls == 0
    assert send.messages[0]["status"] == 413
    body = send.messages[1]["body"]
    assert json.loads(body) == {
        "error": {
            "code": REQUEST_BODY_TOO_LARGE,
            "message": "Request body exceeds the size limit",
            "details": {"maxBytes": LIMIT},
        }
    }
    assert b"SECRET-PAYLOAD" not in body
    assert _headers(send)["x-request-id"] == "req-123"
    _assert_refusal_headers(send)


def test_unparseable_huge_content_length_is_refused_before_receive() -> None:
    downstream = Downstream()
    receive = Receive(*_messages([b"x"]))
    send = _run(
        BodyLimitMiddleware(downstream, max_bytes=LIMIT),
        _http_scope((b"content-length", b"9" * 40)),
        receive,
    )
    assert receive.calls == 0
    assert downstream.calls == 0
    assert send.messages[0]["status"] == 413


def test_chunked_overflow_stops_reading_and_never_dispatches() -> None:
    downstream = Downstream()
    receive = Receive(*_messages([b"a" * 10, b"b" * 10, b"c"]))
    send = _run(BodyLimitMiddleware(downstream, max_bytes=LIMIT), _http_scope(), receive)
    assert receive.calls == 2
    assert downstream.calls == 0
    assert send.messages[0]["status"] == 413
    _assert_refusal_headers(send)


def test_inaccurate_content_length_does_not_bypass_the_count() -> None:
    downstream = Downstream()
    receive = Receive(*_messages([b"a" * 10, b"b" * 10]))
    send = _run(
        BodyLimitMiddleware(downstream, max_bytes=LIMIT),
        _http_scope((b"content-length", b"3")),
        receive,
    )
    assert downstream.calls == 0
    assert send.messages[0]["status"] == 413


def test_exact_limit_arrives_byte_for_byte() -> None:
    downstream = Downstream()
    receive = Receive(*_messages([b"a" * 5, b"b" * 5, b"c" * 6]))
    send = _run(BodyLimitMiddleware(downstream, max_bytes=LIMIT), _http_scope(), receive)
    assert send.messages[0]["status"] == 200
    assert downstream.body == b"a" * 5 + b"b" * 5 + b"c" * 6
    assert downstream.calls == 1


def test_below_limit_bytes_survive_empty_and_split_chunks() -> None:
    downstream = Downstream()
    receive = Receive(*_messages([b"", b'{"ok":', b"true}"]))
    send = _run(BodyLimitMiddleware(downstream, max_bytes=LIMIT), _http_scope(), receive)
    assert send.messages[0]["status"] == 200
    assert downstream.body == b'{"ok":true}'


def test_replay_list_stays_bounded_for_one_byte_chunks() -> None:
    payload = bytes(index % 251 for index in range(130_000))
    downstream = Downstream()
    receive = Receive(*_messages(payload[index : index + 1] for index in range(len(payload))))
    send = _run(BodyLimitMiddleware(downstream, max_bytes=200_000), _http_scope(), receive)
    assert send.messages[0]["status"] == 200
    assert downstream.body == payload
    assert len(downstream.pieces) <= 3


def test_disconnect_before_completion_does_not_dispatch() -> None:
    downstream = Downstream()
    receive = Receive({"type": "http.request", "body": b"abc", "more_body": True})
    send = _run(BodyLimitMiddleware(downstream, max_bytes=LIMIT), _http_scope(), receive)
    assert downstream.calls == 0
    assert send.messages[0]["status"] == 400
    assert json.loads(send.messages[1]["body"])["error"]["code"] == REQUEST_BODY_INCOMPLETE
    _assert_refusal_headers(send)


def test_lifespan_scope_passes_through_unchanged() -> None:
    downstream = Downstream()
    receive = Receive()
    send = _run(BodyLimitMiddleware(downstream, max_bytes=LIMIT), {"type": "lifespan"}, receive)
    assert downstream.calls == 1
    assert downstream.receive_seen is receive
    assert send.messages == []


def test_http2_refusal_has_no_connection_header_and_replaces_invalid_request_id() -> None:
    scope = _http_scope((b"content-length", b"17"), (b"x-request-id", b"bad value"))
    scope["http_version"] = "2"
    send = _run(BodyLimitMiddleware(Downstream(), max_bytes=LIMIT), scope, Receive())
    assert send.messages[0]["status"] == 413
    assert "connection" not in _headers(send)
    assert _headers(send)["x-request-id"].startswith("req_")


def test_request_id_keeps_only_bounded_tracing_tokens() -> None:
    assert safe_request_id("req-client_123") == "req-client_123"
    for value in (None, "", "bad value", "x" * 97, "line\nbreak"):
        generated = safe_request_id(value)
        assert generated.startswith("req_") and len(generated) == 36


def _settings() -> Settings:
    return Settings(
        RELAYPAY_DATABASE_URL="postgresql://unused",
        PROVIDER_DATABASE_URL="postgresql://unused",
        RECEIVER_DATABASE_URL="postgresql://unused",
        SESSION_SECRET="s" * 32,
        CSRF_SECRET="c" * 32,
        API_KEY_PEPPER="a" * 32,
        IDEMPOTENCY_KEY_PEPPER="i" * 32,
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused",
        PROVIDER_SIGNING_SECRET="p" * 16,
        PROVIDER_CONTROL_SECRET="q" * 16,
        RECEIVER_WEBHOOK_SECRET="r" * 16,
        EDGE_ORIGIN_SIGNATURE_REQUIRED=True,
        EDGE_ORIGIN_SIGNING_SECRET="edge-test-secret",
    )


def test_fastapi_chunked_overflow_is_refused_before_the_route_runs() -> None:
    app = FastAPI()
    install_request_body_limit(app, max_bytes=LIMIT)
    calls: list[int] = []

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, str]:
        calls.append(1)
        return {"body": (await request.body()).decode()}

    response = TestClient(app).post("/echo", content=iter([b"a" * 10, b"b" * 10]))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == REQUEST_BODY_TOO_LARGE
    assert response.headers["cache-control"] == "no-store"
    assert calls == []


def test_fastapi_exact_limit_body_reaches_the_route_unchanged() -> None:
    app = FastAPI()
    install_request_body_limit(app, max_bytes=LIMIT)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, str]:
        return {"body": (await request.body()).decode()}

    body = b"x" * (LIMIT - 1) + b"\n"
    response = TestClient(app).post("/echo", content=body)
    assert response.status_code == 200
    assert response.json() == {"body": body.decode()}


def test_cap_runs_before_the_edge_signature_reads_the_body() -> None:
    settings = _settings()
    app = FastAPI()
    install_edge_origin_boundary(app, settings)
    install_request_body_limit(app, max_bytes=LIMIT)

    @app.post("/api/v1/proof")
    async def proof(request: Request) -> dict[str, str]:
        return {"body": (await request.body()).decode()}

    client = TestClient(app)
    oversized = client.post("/api/v1/proof", content=b"{" * (LIMIT + 1))
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == REQUEST_BODY_TOO_LARGE

    unsigned = client.post("/api/v1/proof", content=b'{"s":1}')
    assert unsigned.status_code == 401

    body = b'{"s":1}'
    headers = {
        "x-relaypay-edge-key-id": "v1",
        "x-relaypay-edge-timestamp": str(int(time.time())),
        "x-relaypay-edge-nonce": "1" * 32,
        "x-relaypay-edge-body-sha256": hashlib.sha256(body).hexdigest(),
        "traceparent": "00-" + "2" * 32 + "-" + "3" * 16 + "-01",
    }
    headers["x-relaypay-edge-signature"] = sign_origin_request(
        secret="edge-test-secret",
        method="POST",
        target="/api/v1/proof",
        timestamp=headers["x-relaypay-edge-timestamp"],
        nonce=headers["x-relaypay-edge-nonce"],
        body_sha256=headers["x-relaypay-edge-body-sha256"],
        traceparent=headers["traceparent"],
    )
    signed = client.post("/api/v1/proof", content=body, headers=headers)
    assert signed.status_code == 200
    assert signed.json() == {"body": body.decode()}


def test_dispute_network_refuses_declared_oversize_package_before_submit() -> None:
    network = DeterministicDisputeNetwork()
    app = create_dispute_app(network)
    receive = Receive(*_messages([b"PK"]))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/submissions",
        "headers": [
            (b"content-length", str(MAX_PACKAGE_BYTES + 1).encode()),
            (b"idempotency-key", b"stable-key-0001"),
            (b"content-type", b"application/zip"),
        ],
        "query_string": b"",
    }
    send = _run(app, scope, receive)
    assert send.messages[0]["status"] == 413
    assert receive.calls == 0
    assert network.effect_count == 0


def test_dispute_network_accepts_a_small_package_byte_for_byte() -> None:
    network = DeterministicDisputeNetwork()
    client = TestClient(create_dispute_app(network))
    response = client.post(
        "/v1/submissions",
        content=b"PK\x03\x04synthetic",
        headers={"Idempotency-Key": "stable-key-0002", "Content-Type": "application/zip"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCEEDED"
    assert network.effect_count == 1
