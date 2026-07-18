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

## v0.1.0 release-candidate evidence

Measured at `2026-07-18T11:45:48Z` (`2026-07-18T17:15:48+05:30`) on tested implementation
commit `31b39eb4b318e48af0d3ccefef020324db689eb7`. The evidence-only documentation commit that
contains this record does not change the tested runtime.

- Clean state: `docker compose --env-file .env.example down --volumes --remove-orphans` removed
  only the `relaypay` project containers, network, and `relaypay_postgres_data` volume. Empty
  PostgreSQL and Redis services then became healthy; all three migration histories applied and the
  two synthetic organisations seeded successfully.
- Backend: Ruff lint passed, Ruff reported `95 files already formatted`, strict mypy reported
  `Success: no issues found in 67 source files`, and pytest reported `64 passed in 36.55s`.
- Database: RelayPay, provider, and receiver Alembic drift checks each reported
  `No new upgrade operations detected`.
- Console: ESLint, TypeScript, and the production Next.js build passed. The registry-backed
  `npm audit --omit=dev --audit-level=high` reported `found 0 vulnerabilities`.
- Compose: the development configuration and the combined base plus production overlay both
  validated successfully.
- Lost-response CLI: one provider effect, capture, balanced journal, event, recipient, delivery,
  and receiver row were measured; two compatible idempotency records had stable replay bytes.
- Browser: the single Playwright proof journey, including axe analysis, passed in `8.8s`.
- Video: `scripts/video/build_release_video.sh` reran the proof in `8.6s` and created an untracked
  1440×900 H.264 MP4 at `output/release-video/relaypay-v0.1.0-proof.mp4`; measured duration was
  `14.766667` seconds and size was `518163` bytes. The MP4 is a release attachment, not Git input.
- Publication safety: the tracked-file scan found no private-key blocks or common GitHub, OpenAI,
  or AWS credential patterns. The repository warning continues to prohibit real data.

Tooling: Python test runtime `3.12.12`, uv `0.10.4`, Node.js `25.6.1`, npm `11.9.0`, Docker
`29.5.2`, Docker Compose `5.1.3`, and ffmpeg `8.0.1`.

## Final publication evidence

M0 was published at `2026-07-18T12:36:54Z` after the corrected clean-environment proof passed
on both the release-fix branch and merged `main`:

- Fix PR: [#1](https://github.com/marsh15/relaypay/pull/1), with release gate
  [29644434857](https://github.com/marsh15/relaypay/actions/runs/29644434857) passing in
  `2m36s`.
- Canonical `main` gate:
  [29644539940](https://github.com/marsh15/relaypay/actions/runs/29644539940) passed in `3m01s`
  on commit `d99bfee3bbe4a829a5d59bada70f7ca152123c6a`.
- Annotated tag `v0.1.0` has tag-object SHA
  `283b406be9f3f4890472e00da429055c5e9c6362` and resolves to that green `main` commit.
- Public release: [RelayPay v0.1.0 — Proven baseline](https://github.com/marsh15/relaypay/releases/tag/v0.1.0).
- Release asset: `relaypay-v0.1.0-proof.mp4`, `518163` bytes, SHA-256
  `42a6d2d66fa77f7f6b5789d6322d87f2d9913dc55f9f18d3734e7ea1fdbc6166`.
- GitHub reports the repository as public with the MIT license. The release is final, not a draft
  or prerelease, and retains the synthetic-data warning in its notes.

## Historical blocked rerun (not passing evidence)

The full application image rebuild contacted Docker Hub and GHCR twice. Both attempts timed out
while loading base-image metadata for `ghcr.io/astral-sh/uv:0.10.4`; no compile or application
step failed. The service graph was therefore run directly from the same checkout against the
clean Compose PostgreSQL/Redis state for the CLI, browser, axe, and video results above. CI remains
the canonical clean image-build gate, and publication must not proceed unless that remote gate is
green.

The later canonical GitHub runs listed above resolved this publication gate. The local timeout
remains documented as historical evidence and is not represented as a successful local image run.
