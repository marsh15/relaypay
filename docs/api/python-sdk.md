# RelayPay Python SDK

The public `relaypay_sdk` package is a stable hand-written facade over the private client generated
from `contracts/openapi/v0.7.0.json`. Generation uses pinned `openapi-python-client==0.29.0`;
CI rejects OpenAPI or generated-code drift.

```python
from relaypay_sdk import RelayPayClient

with RelayPayClient(
    base_url="http://localhost:8080",
    api_key="rpk_test_example.secret",
) as client:
    for payment in client.iter_payment_intents(page_size=25):
        print(payment.id)
```

The facade validates TEST/LIVE_LIKE key markers, creates bounded `IdempotencyKey` values, follows
opaque cursors, raises `RelayPayAPIError`, classifies retry safety, and verifies webhook
signatures over exact request bytes. Applications must not decode or construct cursors.

Run the offline exact-byte example:

```bash
uv run python examples/python/verify_webhook.py
```

With the local receiver running, send one signed synthetic event:

```bash
uv run python examples/python/webhook_simulator.py
```

The network example requires the local synthetic stack and `RELAYPAY_TEST_API_KEY`:

```bash
uv run python examples/python/list_payment_intents.py
```
