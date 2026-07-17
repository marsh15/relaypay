# Release test evidence

The release gate is encoded in [CI](../.github/workflows/ci.yml) and runs from empty PostgreSQL
databases. It installs locked dependencies, applies all three Alembic histories, seeds both tenants,
runs backend lint/type/tests, builds and audits the console, starts the complete service graph, and
runs exactly one Playwright lost-response journey with an axe accessibility scan.

## Required local evidence

```bash
make lint
make typecheck
make test
make console-check
make console-e2e
uv run alembic -c migrations/relaypay/alembic.ini check
uv run alembic -c migrations/provider/alembic.ini check
uv run alembic -c migrations/receiver/alembic.ini check
```

The backend suite covers idempotency/key reuse, creation/singleton/refund races, provider outcome
classification, `SENT`-before-HTTP, lookup-only recovery, lease reclamation, shared finalization,
ledger invariants/immutability, event bytes/recipient snapshots, delivery retry/deduplication,
tenant/session/scope/CSRF/redaction/rate-limit boundaries, and the durable scenario API.

The browser test covers safe login redirect, deterministic scenario execution, all proof
assertions, payment evidence, capture-key byte identity, balanced ledger, acknowledged delivery,
and automated WCAG rule scanning. Manual 320px and 200% zoom checks are part of the release review.

## Recorded release-candidate results

- Backend: `64 passed in 90.43s`; final scenario/delivery focus: `4 passed in 17.43s`.
- Browser: the single lost-response Playwright journey passed in `8.5s`, including axe scanning.
- Console: ESLint, TypeScript, and the production Next.js build passed; `npm audit --omit=dev`
  reported zero vulnerabilities.
- Database: all three Alembic drift checks reported `No new upgrade operations detected`.
- Operations: the guarded coordinated reset restored both synthetic organisations successfully.
- Packaging: development and production Compose configurations validated, and the API, provider,
  receiver, worker, and console production images built successfully.

Docker Desktop entered PostgreSQL crash recovery during a later redundant local rerun. That rerun
is not counted as release evidence; the CI workflow recreates the databases from empty volumes and
is the canonical clean-environment gate.
