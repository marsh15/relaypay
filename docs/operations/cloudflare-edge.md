# Cloudflare edge extension

RelayPay's optional edge Worker fronts synchronous `/api/v1` traffic and inbound connector
webhooks without becoming business truth. It is a local/CI proof and is not deployed by this
repository.

Synchronous merchant calls validate the API-key shape, body bounds, replay timestamp/key, and a
per-key-prefix tenant rate window in a Durable Object. The Worker then forwards the original
request to the origin with an HMAC over method, exact path/query, timestamp, nonce, body digest,
and W3C `traceparent`. Set `EDGE_ORIGIN_SIGNATURE_REQUIRED=true` at an origin reachable only from
the Worker to reject bypass traffic.

Inbound webhook bytes are validated, SHA-256 addressed, and written to R2 before a bounded
reference is sent to Cloudflare Queues. The Queue consumer rereads and verifies those bytes, signs
the origin request, and explicitly acknowledges success or requests redelivery after an outage.
PostgreSQL remains the only processing authority; R2 is an immutable archive and Queues may
duplicate delivery.

For local proof, keep the signing secret out of Git:

```text
apps/edge/.dev.vars
ORIGIN_SIGNING_SECRET="replace-with-at-least-32-random-bytes"
```

Then run `npm ci --prefix apps/edge` and `make edge-check`. The gate runs lint, strict TypeScript,
failure/security tests, a Wrangler production-bundle dry run using local Queue/R2/Durable Object
bindings, and a high-severity runtime dependency audit. No deploy command is run.

**Synthetic data only. Never send real payment, banking, identity, or customer data.**
