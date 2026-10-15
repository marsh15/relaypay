# RelayPay Python SDK

The public `relaypay_sdk` package (`packages/relaypay_sdk`) is a hand-written facade over a private
client generated from [`contracts/openapi/v0.9.0.json`](../../contracts/openapi/v0.9.0.json). The
generator is pinned (`openapi-python-client==0.29.0`, configured in `openapi-python-client.yml`)
and `make sdk-drift` fails if the generated code no longer matches the contract. The contract
file name and the generator's `package_version_override: 0.9.0` predate the 1.x releases; the
contract's `info.version` is `1.0.0`.

```python
from relaypay_sdk import RelayPayClient

with RelayPayClient(
    base_url="http://localhost:8000",
    api_key="rpk_test_example.secret",
) as client:
    for payment in client.iter_payment_intents(page_size=25):
        print(payment.id)
```

The API listens on port 8000 in the development stack. Nothing listens on 8080, even though
`PUBLIC_BASE_URL` defaults to it.

## What the facade covers

| Export | Purpose |
| --- | --- |
| `RelayPayClient` | `list_payment_intents`, `iter_payment_intents` (follows cursors), `create_payment_intent` (always `INR`; the caller supplies the idempotency key). No other endpoints are wrapped; use the generated client for the rest. |
| `APIKey` | Validates the `rpk_test_` or `rpk_live_like_` prefix and exposes `.environment` |
| `IdempotencyKey` | Bounded value (1 to 255 characters); `IdempotencyKey.new()` generates `rpi_` plus a random token |
| `PaymentIntent`, `PaymentIntentPage` | Typed results |
| `RelayPayAPIError` | Raised for non-success responses; carries `status_code`, `code`, `message`, `details`, `request_id` (from `X-Request-ID`), and `retry_after`. A body that does not match the error envelope yields code `UNEXPECTED_RESPONSE` |
| `retry_guidance`, `RetryGuidance` | Treats 429, 502, 503, and 504 as transient, and only for `GET`/`HEAD`/`OPTIONS` or a mutation that carries an idempotency key. The client itself does not retry |
| `verify_webhook` | `verify_webhook(body=, timestamp=, signature=, secret=)` checks a `v1=` signature over `"<timestamp>." + body` with a constant-time comparison; call it before parsing the JSON |

Applications must not decode or construct pagination cursors.

## Examples

Offline verification of an exact-byte webhook signature:

```bash
uv run python examples/python/verify_webhook.py
```

Sending one signed synthetic event to the local receiver. The script's default secret
(`dev-receiver-webhook-secret`) does not match the development stack's
`RECEIVER_WEBHOOK_SECRET` (`dev-receiver-secret-change-me`), so pass it explicitly:

```bash
uv run python examples/python/webhook_simulator.py --secret dev-receiver-secret-change-me
```

The event body in both examples is a minimal fixture (`type: payment.captured`), not the shape
RelayPay emits (`payment.captured.v1` with `schemaVersion`, `occurredAt`, and `data`); the
receiver only requires the body `id` to match the header.

Listing payment intents needs the running stack and a TEST key (see the README for how to read the
seeded key). The script defaults `RELAYPAY_BASE_URL` to port 8080, so set it:

```bash
RELAYPAY_BASE_URL=http://localhost:8000 RELAYPAY_TEST_API_KEY=rpk_test_... \
  uv run python examples/python/list_payment_intents.py
```
