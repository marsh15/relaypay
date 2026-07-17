# Threat model

## Scope and assets

The protected assets are tenant boundaries, opaque sessions and CSRF tokens, API-key material,
provider/webhook signing secrets, idempotency fingerprints and terminal bytes, financial journals,
event bytes/digests, and delivery/recovery leases. All data is synthetic, but the sandbox preserves
production-style integrity properties.

## Trust boundaries and controls

| Boundary | Principal risk | Implemented controls |
| --- | --- | --- |
| Browser → console/API | session theft, CSRF, open redirect, secret persistence | opaque HttpOnly SameSite cookies, Secure in production, CSRF on session mutations, narrow redirect allowlist, CSP/security headers, no local storage |
| Merchant API → RelayPay | tenant access, replay/key confusion, malformed money | peppered scoped API keys, route-aware idempotency fingerprint, strict DTOs, INR integer paise, tenant composite keys |
| RelayPay → provider | duplicated effect, forged/contradictory result | `SENT` commit before HTTP, unique stable key, mutation never resent, signed/schema/account/kind/amount/currency/reference validation |
| RelayPay → receiver | SSRF, tampering, duplicate consumer effect | exact configured URL allowlist, no redirects, timestamp+body HMAC, immutable event bytes/digest, receiver event-ID/digest deduplication |
| Workers → PostgreSQL | duplicate claims, stale acknowledgement | short transactions, `SKIP LOCKED`, random lease token, expiry/reclamation, shared idempotent finalizer |
| Tenant → evidence | cross-tenant records or secret leakage | organisation predicate on reads, admin-only session surface, `404` for foreign IDs, bounded collections, redacted key hints and safe error codes |
| Database roles | cross-database/schema access | distinct app/migrator roles, revoked public connect/create, isolated receiver schema, composite tenant foreign keys |

## Abuse resistance

Login and sensitive routes are rate limited with `Retry-After`. Evidence collections are capped at
100 rows. Provider control endpoints require a dedicated secret and are not routed by Caddy.
Webhook replay and recovery retry require an administrator session plus CSRF confirmation.

## Residual risks

This project does not provide a WAF, managed secret store, hardware-backed keys, fraud controls,
multi-region consensus, disaster-recovery automation, audit-log export, malware scanning, or real
payment-network certification. Docker secrets and a managed database should replace environment
variables for any environment beyond a disposable portfolio sandbox. See [limitations](limitations.md).
