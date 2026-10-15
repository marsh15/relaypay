# Configuration

RelayPay is configured entirely through environment variables. `packages/relaypay/config.py`
(`Settings`, pydantic-settings) reads them from the process environment and, for host-run code,
from `.env`. Unknown variables are ignored. Copy `.env.example` to `.env` for local use and
`.env.production.example` to `.env.production` for the production overlay; both contain
placeholders only. Never commit the filled-in files.

Containers do **not** read `.env` (`.dockerignore` excludes it, and no service uses `env_file`).
They receive only the variables listed in the `x-app-environment` block of `compose.yaml`
(with values taken from the Compose project environment), so a variable that is not in that
block cannot be set for containers without editing the Compose file.

## Settings

"Secret" means the value is a credential or key. Required settings have no default and make the
process fail at start-up when absent.

### General

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | `development`, `test`, or `production`. Production requires an HTTPS `PUBLIC_BASE_URL`, sets `Secure` on the session cookie, and rejects development defaults for the eight secrets listed below |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Public origin of the deployment. Nothing binds port 8080; it is only validated in production |
| `LOG_LEVEL` | `INFO` | Declared but not read anywhere in the code |
| `SESSION_COOKIE_NAME` | `relaypay_session` | Admin session cookie name |

### Databases

| Variable | Default | Secret | Purpose |
| --- | --- | :---: | --- |
| `RELAYPAY_DATABASE_URL` | required | yes | App connection for the `relaypay` database |
| `RELAYPAY_MIGRATION_DATABASE_URL` | none | yes | Owner connection for Alembic and `make reset`; reset falls back to the app URL |
| `PROVIDER_DATABASE_URL`, `PROVIDER_MIGRATION_DATABASE_URL` | required, none | yes | Provider simulator database |
| `BANK_DATABASE_URL`, `BANK_MIGRATION_DATABASE_URL` | local development DSN, none | yes | Bank simulator database |
| `COMMERCE_DATABASE_URL`, `COMMERCE_MIGRATION_DATABASE_URL` | local development DSN, none | yes | Commerce simulator database |
| `RECEIVER_DATABASE_URL` | required | yes | `receiver_app` connection to the `relaypay` database; also used by the receiver Alembic tree |

Use the `postgresql+psycopg://` scheme. The development DSNs point at `localhost:55432`.

### Credentials and keys

| Variable | Minimum | Purpose |
| --- | --- | --- |
| `SESSION_SECRET` | 32 chars | HMAC key for session token digests |
| `CSRF_SECRET` | 32 chars | HMAC key for CSRF tokens |
| `API_KEY_PEPPER` | 32 chars | Pepper for API key digests; also derives the pagination cursor key |
| `IDEMPOTENCY_KEY_PEPPER` | 16 chars | Pepper for idempotency key digests |
| `WEBHOOK_SECRET_ENCRYPTION_KEY` | required | Key material for AES-256-GCM encryption of webhook endpoint secrets (SHA-256 derived, so any string works; it is **not** a Fernet key despite the placeholder text in the example files) |
| `CONNECTOR_CREDENTIAL_ENCRYPTION_KEY` | dev default | Encrypts connector credentials |
| `DISPUTE_PACKAGE_SIGNING_SECRET` | dev default | Signs dispute packages |

All of these are secrets. Changing a pepper invalidates every stored digest (API keys, idempotency
keys, cursors); rotating an encryption key without re-encrypting makes stored secrets unreadable.

### Counterparty services

| Variable | Default | Secret | Purpose |
| --- | --- | :---: | --- |
| `PROVIDER_BASE_URL`, `PROVIDER_ACCOUNT_ID` | `http://localhost:8001`, `acct_relaypay_demo` | no | Where RelayPay calls the provider and which account it uses |
| `PROVIDER_SIGNING_SECRET` | required, 16+ chars | yes | Verifies provider response signatures |
| `PROVIDER_CONTROL_SECRET` | required, 16+ chars | yes | Authorizes provider `/control/*` calls |
| `BANK_BASE_URL`, `BANK_ACCOUNT_ID` | `http://localhost:8003`, `bank_relaypay_demo` | no | Bank simulator |
| `BANK_SIGNING_SECRET`, `BANK_CONTROL_SECRET` | dev defaults | yes | Bank response signature and control |
| `COMMERCE_BASE_URL`, `COMMERCE_ACCOUNT_ID` | `http://localhost:8004`, `commerce_relaypay_demo` | no | Commerce simulator |
| `COMMERCE_CONTROL_SECRET` | dev default | yes | Commerce control calls; also the HMAC key for inbound connector events |
| `DISPUTE_NETWORK_BASE_URL` | `http://localhost:8005` | no | Dispute network simulator |
| `RECOVERY_NETWORK_BASE_URL` | `http://localhost:8006` | no | Message and retry simulator |
| `RISK_SITE_BASE_URL` | `http://localhost:8007` | no | Site-snapshot simulator |
| `RECEIVER_BASE_URL` | `http://localhost:8002` | no | The only webhook destination; deliveries go to `{RECEIVER_BASE_URL}/webhooks/relaypay` |
| `RECEIVER_WEBHOOK_SECRET` | required, 16+ chars | yes | Signing secret of the bundled receiver endpoint |
| `INBOUND_WEBHOOK_REPLAY_SECONDS` | `300` | no | Replay window for inbound connector events |
| `REDPANDA_BROKERS` | `localhost:19092` | no | Outbox publish target (`redpanda:9092` in Compose) |

### Observability and edge

| Variable | Default | Purpose |
| --- | --- | --- |
| `OBSERVABILITY_ENABLED` | `false` | Enables `/metrics` and OpenTelemetry tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4318` | OTLP/HTTP endpoint |
| `PROMETHEUS_WORKER_PORT` | `9100` | Poller metrics port (1024 to 65535) |
| `REQUEST_LOG_RETENTION` | `10000` | Approximate cap on raw request-log rows (100 to 1,000,000), enforced by probabilistic pruning |
| `EDGE_ORIGIN_SIGNATURE_REQUIRED` | `false` | Require the edge HMAC on `/api/v1*` and `/api/inbound/v1*` |
| `EDGE_ORIGIN_SIGNING_SECRET` | dev default | Secret shared with the Worker's `ORIGIN_SIGNING_SECRET`; rejected at start-up while enforcement is on and the value is still the default |
| `EDGE_ORIGIN_REPLAY_SECONDS` | `300` | Timestamp window, 30 to 900 |

## Production validation

With `APP_ENV=production`, start-up fails if `PUBLIC_BASE_URL` is not HTTPS or if any of these still
holds its development default: `PROVIDER_SIGNING_SECRET`, `PROVIDER_CONTROL_SECRET`,
`BANK_SIGNING_SECRET`, `BANK_CONTROL_SECRET`, `COMMERCE_CONTROL_SECRET`,
`CONNECTOR_CREDENTIAL_ENCRYPTION_KEY`, `DISPUTE_PACKAGE_SIGNING_SECRET`,
`RECEIVER_WEBHOOK_SECRET`. The session, CSRF, pepper, and `WEBHOOK_SECRET_ENCRYPTION_KEY` values
have no default in `Settings`, but Compose supplies development values when unset, which is why
`compose.production.yaml` requires every secret with `${VAR:?}`.

## Variables outside `Settings`

| Variable | Used by | Notes |
| --- | --- | --- |
| `API_HOST_PORT`, `CONSOLE_HOST_PORT` | `compose.yaml` | Host ports for the API (default 8000) and console (default 3000) |
| `POSTGRES_PASSWORD` and `*_DB_PASSWORD` | Compose, `infra/postgres/init` | Passwords for the superuser and the migrator and app roles (`RELAYPAY_`, `PROVIDER_`, `BANK_`, `COMMERCE_`, plus `RECEIVER_APP_`). `.env.example` omits the two bank ones; Compose defaults them |
| `CADDY_DOMAIN`, `CADDY_ACME_EMAIL` | Caddy | Required by the production overlay |
| `INTERNAL_API_BASE_URL` | Console | Where server components and the `/backend/*` proxy reach the API (default `http://localhost:8000`; Compose sets `http://api:8000`) |
| `SANDBOX_RESET_CONFIRM`, `ALLOW_SANDBOX_RESET` | `scripts/reset_sandbox.py` | Must be `RESET_SYNTHETIC_RELAYPAY`; production also needs `true` |
| `RELAYPAY_BOOTSTRAP_ADMIN_EMAIL`, `_PASSWORD`, `_NAME` | `scripts/bootstrap_platform_admin.py` | Creates or promotes a `PLATFORM_ADMIN` user |
| `RELAYPAY_DEMO_EMAIL`, `RELAYPAY_DEMO_PASSWORD`, `RELAYPAY_API_BASE_URL` | `scripts/reconciliation_demo.py` | Console-login credentials for the demo |
| `RELAYPAY_TEST_API_KEY`, `RELAYPAY_BASE_URL` | `examples/python` | See the [SDK guide](api/python-sdk.md) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | `scripts/compare_live_providers.py` | Release-only comparison; inert without keys; never run in CI |
| `PLAYWRIGHT_BASE_URL`, `PLAYWRIGHT_CHANNEL`, `CI` | Console e2e | |
| `ORIGIN_SIGNING_SECRET` | Edge Worker | Set in `apps/edge/.dev.vars` locally; see [cloudflare-edge](operations/cloudflare-edge.md) |

## Gaps to know about

- Compose does not forward `LOG_LEVEL`, `SESSION_COOKIE_NAME`, `INBOUND_WEBHOOK_REPLAY_SECONDS`,
  `REQUEST_LOG_RETENTION`, `PROMETHEUS_WORKER_PORT`, or any `EDGE_*` variable to containers. They
  take effect only for processes you run on the host.
- `INBOUND_WEBHOOK_REPLAY_SECONDS` is absent from both example files.
- `.env.production.example` includes `APP_ENV`, `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `PROMETHEUS_WORKER_PORT`, and `REQUEST_LOG_RETENTION`, none of which changes container behavior
  (the overlay fixes `APP_ENV`, and the others are not forwarded). `OBSERVABILITY_ENABLED` does.
- `NEXT_PUBLIC_API_BASE_URL` appears in `.env.example` but nothing reads it.
