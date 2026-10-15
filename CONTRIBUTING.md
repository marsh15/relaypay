# Contributing

RelayPay is synthetic-data software. Do not add real card, bank, identity, or customer data to
code, fixtures, tests, or documentation, and do not commit filled-in `.env` files or real
credentials.

## Set up

Follow [Local development](README.md#local-development) in the README: Python 3.12 with `uv`,
Node.js 24, and Docker for PostgreSQL and Redpanda.

```bash
uv sync --frozen
docker compose up -d postgres redpanda
make migrate && make seed
make check          # needs PostgreSQL and Redpanda; the Playwright journeys need the full stack
```

`make check` is the local approximation of the CI job in `.github/workflows/ci.yml`. CI is the
authority: it also replays the per-milestone upgrade scripts (`scripts/verify_m*_upgrade.py`),
runs the evaluation suite, and measures performance evidence.

## Conventions

- **Python.** `ruff` (line length 100; rules E, F, I, B, UP, RUF, SIM, S) and strict `mypy` over
  `packages`, `apps`, and `scripts`. Code in `packages/relaypay` is organised by domain
  (`payments`, `ledger`, `event_delivery`, and so on), each with `models.py` and `service.py`.
- **Tests.** `tests/unit` needs no services; `tests/integration` needs PostgreSQL, and the agent
  runtime tests also need Redpanda. Markers: `integration`, `concurrency`. Add a test for every
  behavior change and for every race or recovery path you touch.
- **Console and edge.** `npm run lint`, `typecheck`, and `build` in `apps/console`;
  `make edge-check` for the Worker.
- **Commits.** No message format is enforced. History uses short imperative subjects with the PR
  number.

## Invariants to preserve

These rules carry the system's correctness; changes that bend them need an ADR.

1. **Send-before-HTTP, lookup-only after.** A provider, bank, or network mutation commits its
   operation and `SENT` attempt before any HTTP, and is never resent for the same stable key.
   Recovery only looks up.
2. **No network I/O inside a transaction that holds locks.** Claim in one short transaction,
   call out, finalize in another with the lease token.
3. **One finalizer.** Terminal state, ledger journal, event, and every idempotency key's stored
   response are written in one transaction.
4. **Immutable evidence.** Do not add code that updates or deletes journals, postings, events,
   statements, or attempts. New evidence tables should get the same `BEFORE UPDATE OR DELETE`
   triggers as the older ones (migrations 0012 to 0017 did not; see
   [database](docs/database.md#immutability)).
5. **Tenant scoping.** Every query filters by organisation and environment, tables use composite
   foreign keys, and foreign identifiers return `404`.
6. **Idempotency is route-aware.** The fingerprint covers version, method, route template, path
   parameters, and body.

## Changing the schema

1. Add an Alembic revision to the tree that owns the database (`migrations/relaypay`,
   `provider`, `bank`, `commerce`, or `receiver`). Use the next `NNNN_name` revision id and keep
   the chain linear.
2. Run `uv run alembic -c migrations/<tree>/alembic.ini check`; it must report no drift. Test an
   upgrade from the previous release against existing rows, as the `verify_m*_upgrade` scripts do.
3. Update [docs/database.md](docs/database.md).

## Changing the API

- `/api/v1` is frozen against `contracts/openapi/api-v1-v0.6.0.json`; additive changes only.
  `make api-contract` regenerates both documents in memory, compares them to the committed files,
  and runs the compatibility check.
- After an intentional change, rewrite the current contract with
  `uv run python -m scripts.generate_openapi`, then run `make api-contract`.
- The generated SDK under `packages/relaypay_sdk/_generated` must match the contract
  (`make sdk-drift`). Regenerate it with `openapi-python-client` and the settings in
  `openapi-python-client.yml`; there is no Make target that writes it.
- Update [docs/api/reference.md](docs/api/reference.md) and the
  [error catalog](docs/api/error-catalog.md) for new routes and error codes.

## Documentation

Documentation changes with the code in the same pull request. Prefer the real command, path, or
setting over a description of it, and verify claims against the source. Record a design decision
as an ADR in `docs/adr/` (add it to the index), use the terms in [CONTEXT.md](CONTEXT.md), and
leave historical records (`docs/releases`, `docs/evidence`, `docs/migrations`, `docs/vibe-coding`,
`docs/phase-2`, `docs/week-4-gate.md`) as written.

The architecture diagram is `docs/diagrams/architecture.excalidraw`; open it in Excalidraw, edit
it, and export the SVG over `docs/diagrams/architecture.svg`.
