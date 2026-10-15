# Ubuntu sandbox deployment

This is an optional single-host, synthetic-data sandbox. It is a disposable portfolio layout, not
a regulated production architecture, and the repository does not host or deploy it for you. Use a
current Ubuntu LTS host with Docker Engine and Compose v2.24 or newer (the overlay uses
`ports: !reset []`), DNS pointed at the host, inbound TCP 80/443 and UDP 443 only, and SSH
restricted to operator addresses.

## Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Replace every `GENERATE_...` value with independent random material. Never reuse development
values. The overlay (`compose.production.yaml`) requires every database password and secret
(`${VAR:?}`), sets `APP_ENV=production` (HTTPS-only `PUBLIC_BASE_URL`, `Secure` session cookies,
rejection of development secrets), removes the host port bindings of PostgreSQL, Redpanda, the API,
the console, and every simulator, and leaves Caddy as the only published service. Variable
reference: [configuration](configuration.md).

## Start

```bash
docker compose \
  --env-file .env.production \
  -f compose.yaml \
  -f compose.production.yaml \
  --profile edge \
  up -d --build --wait
```

The stack runs `postgres`, `redpanda`, the one-shot `migrate`, `api`, `poller`, `console`,
`receiver`, and the six simulators (`provider`, `bank`, `commerce`, `dispute-network`,
`recovery-network`, `merchant-site`), plus `caddy` under the `edge` profile. Caddy obtains and
renews the certificate, redirects HTTP to HTTPS, serves HTTP/3, and routes `/api/*` and
`/health/*` to the API, `/webhooks/relaypay` to the receiver, and everything else to the console.
Provider control routes and `/metrics` are not routed.

Verify `https://$CADDY_DOMAIN/health/live` and `/health/ready`, sign in, run the CSRF-protected
scenario from the Scenario Lab, and confirm the lost-response proof passes.

### Before exposing it

- **Demo credentials are seeded on every start.** The `migrate` service runs `scripts.seed`,
  which creates the two demo organisations with the fixed development passwords published in the
  README if they do not exist. The production overlay does not change that. Change or disable
  those users (for example, rotate their passwords through the database or remove the
  organisations) before the host is reachable by anyone you do not trust.
- **Redpanda** runs in `dev-container` mode with no data volume. It carries only derived events and
  is rebuilt empty on restart; nothing in this repository consumes the topics.
- **Observability.** If you also enable the `observability` profile, its ports (Prometheus 9090,
  Grafana 3001 with anonymous viewer access, collector 4317/4318) are published on the host: the
  overlay does not reset them. Bind them to a private interface or leave the profile off.
- Container hardening in place: non-root application images (`relaypay` uid 10001 for Python,
  `nextjs` uid 1001 for the console), `no-new-privileges`, `init`, and 1 CPU / 512 MB limits.
  Containers are not read-only, drop no capabilities, and the console, poller, and Caddy have no
  health checks.

## Backup and restore posture

The sandbox should be resettable, but daily encrypted logical backups make operational failures
observable. There are four databases to back up; the `relaypay` dump includes the isolated
`receiver` schema.

```bash
C="docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml"
for db in relaypay provider bank commerce; do
  $C exec -T postgres pg_dump -U postgres --format=custom "$db" > "$db.dump"
done
```

Encrypt and move dumps off-host, enforce retention, and test restoration on a separate host. Stop
all application services before restore. Restore with `pg_restore --clean --if-exists` into empty
databases, then run the migration service and readiness checks. Backups are not a substitute for a
managed database with point-in-time recovery.

## Reset and operations

`scripts.reset_sandbox` truncates all four databases and the receiver schema, then reseeds the
demo organisations. It needs `SANDBOX_RESET_CONFIRM=RESET_SYNTHETIC_RELAYPAY` and, in production,
`ALLOW_SANDBOX_RESET=true`. There is no reset service in Compose, and PostgreSQL has no host port
in this layout, so `make reset` from the host cannot connect. Stop every writer, then run the
script from a one-off container (which has the migrator URLs the script prefers):

```bash
$C stop api poller console provider receiver bank commerce \
  dispute-network recovery-network merchant-site
$C run --rm \
  -e SANDBOX_RESET_CONFIRM=RESET_SYNTHETIC_RELAYPAY -e ALLOW_SANDBOX_RESET=true \
  migrate python -m scripts.reset_sandbox
$C up -d --wait
```

This one-off invocation has not been exercised in CI. Rerun the primary proof afterwards. Monitor
container health, disk usage, certificate renewal, restart counts, and backup completion. Rotate
secrets by rebuilding disposable state, or coordinate database role and password changes during
downtime.

The Cloudflare Worker in `apps/edge` is not part of this deployment; see
[operations/cloudflare-edge.md](operations/cloudflare-edge.md).
