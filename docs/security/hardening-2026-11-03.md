# Hardening verification — 2026-11-03

This implements the useful source corrections following the
[baseline audit](audits/2026-11-03/REPORT.md). It does not turn the audit's conditional records into
confirmed vulnerabilities or certify a deployment. RelayPay remains synthetic-only.

## Changes

- Counted request admission: API, receiver and console proxy 2 MiB; dispute counterparty 20 MiB;
  optional edge 1 MiB. Declared oversize requests can be refused without reading; otherwise reads
  stop at the chunk crossing the cap. Signature input bytes are preserved. HTTP/2 refusals omit
  connection-specific headers. Tracing IDs accept bounded tokens rather than arbitrary text.
- HTTP metrics, request logs and durable rollups use route templates or `unmatched`, and standard
  methods or `OTHER`. The inbound scheduler yields after at most 25 claims per scheduled batch.
- Recurring-failure HTTP intake passes an aware datetime into its strict envelope. Consumption
  markers bind scope and payload; duplicate provider attempts also bind invoice, scope, code,
  outcome and evidence. Unrelated integrity failures remain errors. The route is restored together
  with ownership checks, rather than exposing its former latent lookup defect.
- Authorization expiry rechecks capture/void eligibility after acquiring the payment lock. This is
  a defensive correctness change, not a claim that the proposed PostgreSQL race was reproduced.
- Reconciliation reports use private temporary files and atomic replacement, refuse link/nonregular
  destinations, and retain the previous report on rendering failure. A portable fallback preserves
  support without directory-FD APIs; it requires an operator-controlled output directory. Mapping
  JSON is bounded and malformed/deep input produces exit 2; CSV processing remains streamed.
- Production monitoring bindings use loopback. Nested secret/state build-context patterns are
  excluded. The console preserves backend cache policy. Dependency locks and installation policies
  are described in [Python triage](dependencies.md) and [npm triage](node-dependencies.md).

## Local checks

Target execution used read-only staged source, network-disabled containers, dummy data, an empty
allowlisted environment, private scratch, and explicit resource/time/output limits. PostgreSQL
checks used a private loopback-only fixture with no published ports or shared database.

| Check | Observed result |
|---|---|
| Changed unit/reconciliation suites, excluding memory proof | 176 passed |
| Recurring HTTP/service replay, M10 and M12 integration | 14 passed |
| Actual API telemetry and void/expiry integration | 17 passed; telemetry-only recheck 3 passed |
| Edge Vitest suites | 11 passed |
| Console and edge TypeScript checks | Passed |
| Console and edge ESLint | Passed |
| Ruff lint and formatting | Passed, 398 Python files formatted |
| Strict mypy | Passed, 244 source files |
| Current and baseline OpenAPI drift checks | Passed |
| Private Uvicorn HTTP/1.1 admission fixture | Exact-limit 200; declared/chunked oversize 413; subsequent valid request 200 |
| Native production Compose rendering, with/without profiles | Only Caddy public; monitoring ports loopback; observability absent without its profile |
| Native COPY-only build using dummy files | Representative nested secret/state patterns excluded; example templates retained |
| Baseline audit finding/coverage validators | 25 records and 151 coverage units valid |

The application checks above used the existing local toolchains/runtime images. The dependency
updates happened separately: native advisory scans verified the new locks, but these earlier
runtime checks are **not** evidence that the upgraded libraries were installed. Fresh CI frozen
installs and image builds validate that combination. Install scripts are disabled, npm11.19.1 is
pinned, and uv0.12.19 is enforced in the project, image and CI.

## Limits and remaining work

The source review did not reproduce a concurrency failure, availability outage, real Cloudflare
storage semantics, a deployed ingress bypass, or actual Windows behavior. The 2M-row memory proof
was not rerun; its matching algorithm is unchanged. Per-request caps do not bound aggregate
concurrency. Native audit matches known advisories, not malicious-package provenance.

The four implementation groups reserved by [PROVENANCE.md](../../PROVENANCE.md) remain unchanged,
including their intentionally failing handwritten specification tests. They require maintainer
implementation before the entire project and handwritten CI gate can be called complete. The
user's portfolio-demo edit and handoff prompt are excluded from this change.
