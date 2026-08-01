import hashlib
import time

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.edge.origin import install_edge_origin_boundary, sign_origin_request
from relaypay.errors import RelayPayError


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


def _app() -> FastAPI:
    app = FastAPI()
    install_edge_origin_boundary(app, _settings())

    @app.exception_handler(RelayPayError)
    async def handle(_: Request, error: RelayPayError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse({"code": error.code}, status_code=error.http_status)

    @app.post("/api/v1/proof")
    async def proof(request: Request):  # type: ignore[no-untyped-def]
        return {"body": (await request.body()).decode()}

    return app


def test_edge_bypass_and_tampering_are_rejected_but_signed_bytes_and_trace_pass() -> None:
    client = TestClient(_app())
    assert client.post("/api/v1/proof", content=b"{}").status_code == 401
    body = b'{"synthetic":true}'
    timestamp = str(int(time.time()))
    nonce = "1" * 32
    digest = hashlib.sha256(body).hexdigest()
    traceparent = "00-" + "2" * 32 + "-" + "3" * 16 + "-01"
    signature = sign_origin_request(
        secret="edge-test-secret",
        method="POST",
        target="/api/v1/proof",
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=digest,
        traceparent=traceparent,
    )
    headers = {
        "x-relaypay-edge-key-id": "v1",
        "x-relaypay-edge-timestamp": timestamp,
        "x-relaypay-edge-nonce": nonce,
        "x-relaypay-edge-body-sha256": digest,
        "x-relaypay-edge-signature": signature,
        "traceparent": traceparent,
    }
    response = client.post("/api/v1/proof", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"body": body.decode()}
    assert response.headers["x-relaypay-traceparent"] == traceparent
    headers["x-relaypay-edge-signature"] = "0" * 64
    assert client.post("/api/v1/proof", content=body, headers=headers).status_code == 401
