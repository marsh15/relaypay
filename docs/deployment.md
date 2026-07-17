# Ubuntu sandbox deployment

This is an optional single-host, synthetic-data sandbox. Use a current Ubuntu LTS host with Docker
Engine, the Compose plugin, DNS pointed at the host, inbound TCP 80/443 and UDP 443 only, and SSH
restricted to operator addresses.

## Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Replace every `GENERATE_...` value with independent random material. Never reuse development
values. The production overlay rejects missing secrets, removes all direct database/cache/service
ports, enables `APP_ENV=production` (Secure session cookies), and publishes only Caddy.

## Start

```bash
docker compose \
  --env-file .env.production \
  -f compose.yaml \
  -f compose.production.yaml \
  --profile edge \
  up -d --build --wait
```

Verify `https://$CADDY_DOMAIN/health/live`, `/health/ready`, login, CSRF-protected scenario run,
and the lost-response proof. Caddy obtains and renews the public certificate. Provider control,
PostgreSQL, Redis, and worker endpoints have no host bindings.

## Backup and restore posture

The sandbox should be resettable, but daily encrypted logical backups make operational failures
observable. Back up both databases (the RelayPay dump includes the isolated receiver schema):

```bash
docker compose --env-file .env.production exec -T postgres \
  pg_dump -U postgres --format=custom relaypay > relaypay.dump
docker compose --env-file .env.production exec -T postgres \
  pg_dump -U postgres --format=custom provider > provider.dump
```

Encrypt and move dumps off-host, enforce retention, and test restoration on a separate host. Stop
all application services before restore. Restore with `pg_restore --clean --if-exists` into empty
databases, then run the migration service and readiness checks. Backups are not a substitute for a
managed database with point-in-time recovery.

## Reset and operations

For a public synthetic sandbox, stop `api`, `provider`, `receiver`, `worker`, `poller`, and
`console`; then run the reset container with both explicit production guards. Restart the stack and
rerun the primary proof. Monitor container health, disk usage, certificate renewal, restart counts,
and backup completion. Rotate every secret by rebuilding disposable state or coordinating database
role/password changes during downtime.
