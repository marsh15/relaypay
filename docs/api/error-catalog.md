# RelayPay API error catalog

Every API error uses `{"error":{"code","message","details"}}`. Treat `code` as stable and
`message` as human-readable. The `X-Request-ID` response header is safe to record for support.

| Code | HTTP | Meaning | Retry guidance |
|---|---:|---|---|
| `UNAUTHENTICATED` | 401 | Missing, invalid, expired, or revoked credential | Obtain a valid credential |
| `RESOURCE_NOT_FOUND` | 404 | Resource is absent or outside the caller environment | Do not retry unchanged |
| `VALIDATION_ERROR` | 422 | Request failed strict schema validation | Correct the request |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | A financial command omitted its key | Retry with a new key |
| `IDEMPOTENCY_KEY_REUSED` | 409 | The key is bound to different canonical input | Use a new key |
| `INVALID_CURSOR` | 400 | Cursor is malformed, tampered, or bound to different filters | Restart pagination |
| `RATE_LIMITED` | 429 | Caller exceeded a bounded request rate | Honor `Retry-After` |
| `DEPENDENCY_UNAVAILABLE` | 503 | A required dependency is unavailable | Back off; reuse the same command key |

Mutation retries are safe only when the caller reuses the original `Idempotency-Key`. RelayPay
stores terminal response bytes and returns them unchanged on valid replay.
