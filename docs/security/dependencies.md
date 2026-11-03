# Dependency advisory triage (2026-11-03)

Scope: native `uv audit --frozen` findings against `uv.lock` as of 2026-11-03. This is separate
from the baseline code audit and does not change its findings.

Result: 23 advisory entries (alias duplicates of 13 distinct advisories) across 8 packages.
All are remediated by version bumps. None are deferred. A re-run of `uv audit --frozen` on the
updated lockfile reports 0 vulnerabilities and 0 adverse statuses across 92 audited packages.

## Verification status

- Lock resolution: `uv lock --no-build` (metadata only) and `uv lock --check` pass.
- `uv audit --frozen` (public advisory queries only, scratch HOME and cache, empty env): 0 findings.
- Not done by this change: no package was installed, built or executed. Unit, integration and
  image tests against the upgraded libraries are the responsibility of CI and the sandbox test
  run. The upgraded libraries have not been tested against the previous runtime images.

## Advisories and disposition

| Package | Locked (before → after) | Advisory IDs (aliases) | Fixed in | Reachability in RelayPay | Disposition |
|---|---|---|---|---|---|
| certifi | 2026.6.17 → 2026.10.23 | GHSA-6q48-6c7w-9gw2 (PYSEC-2026-4320) | 2026.10.18 (emSign roots restored selectively in 2026.10.19) | Reachable: CA bundle used for outbound TLS by httpx and requests. | Upgraded. Rebuild container images so the new bundle is vendored. |
| cryptography | 46.0.7 → 50.0.2 | GHSA-537c-gmf6-5ccf (statically linked OpenSSL in wheels) | 48.0.1 | Wheels are used. | Upgraded. |
| cryptography | same | GHSA-g6cj-pr64-35w5 (CVE-2026-69247, PYSEC-2026-3552) PKCS#7 EnvelopedData decrypt oracle | 50.0.0 | Not reachable: no `pkcs7_decrypt_*` call. | Fixed by upgrade; also unreachable. |
| cryptography | same | GHSA-jwv3-5hgf-82ww (CVE-2026-69249, PYSEC-2026-3553) path-building blowup | 49.0.0 | Not reachable: no X.509 verifier use. | Fixed by upgrade; also unreachable. |
| cryptography | same | GHSA-m2h6-j472-rp4c (CVE-2026-69248, PYSEC-2026-3554) wildcard name constraint bypass | 49.0.0 | Not reachable: no X.509 verifier use. | Fixed by upgrade; also unreachable. |
| dnspython | 2.8.0 → 2.9.1 | GHSA-45c3-73f7-pv4m (CVE-2026-114605) `from_wire_parser` CPU use | 2.9.0 | Transitive via email-validator (syntax/deliverability checks on addresses). RelayPay code does not parse DNS wire data. Treated as low reachability. | Upgraded. |
| mako | 1.3.12 → 1.4.3 | GHSA-5639-2j2p-m4mx (CVE-2026-102991, PYSEC-2026-4243) Windows drive-letter path traversal in `TemplateLookup` | 1.4.2 | Not reachable: transitive via alembic; migration templates are local and no user-controlled URIs reach `TemplateLookup`. Windows-only. | Upgraded. |
| openapi-python-client | 0.29.0 → 0.29.1 | GHSA-5293-mq8x-g3xj (CVE-2026-105801, PYSEC-2026-4245) code generation from malicious OpenAPI documents | 0.29.1 | Dev-only. Input is the repository's own contract. | Upgraded (pin `==0.29.1`). |
| opentelemetry-api | 1.44.0 → 1.45.1 | GHSA-xcq7-p3pf-p88h (CVE-2026-114603) unbounded `tracestate` parsing | 1.45.1 | Reachable: FastAPI and httpx instrumentation extract W3C trace context from request headers. | Upgraded. |
| pytest | 8.4.2 → 9.2.0 | GHSA-6w46-j5rx-g56g (CVE-2025-71176, PYSEC-2026-1845) tmpdir handling | 9.0.3 | Dev/CI only; shared-tmp attack needs a local hostile user. | Upgraded across the major boundary (see below). |
| urllib3 | 2.7.0 → 2.8.0 | GHSA-8988-9cw3-xx77 (CVE-2026-97687), GHSA-gh4c-6fx4-qh6g (CVE-2026-97688), GHSA-vxq7-64xx-v4gw (CVE-2026-97689), plus PYSEC-2026-4175/4176/4177 | 2.8.0 | Transitive via requests (dev tooling and generated SDK). Not used by httpx. Treated as low to moderate reachability. | Upgraded. |

No reachable high or critical advisory is carried as mitigated; all were fixed.

## Constraint changes in `pyproject.toml`

- `cryptography>=46.0.0,<47` → `>=50.0.0,<51`. Only `AESGCM` and `InvalidTag` are used
  (`packages/relaypay/connectors/crypto.py`, `packages/relaypay/event_delivery/crypto.py`). Per
  the official changelog (cryptography.io), 47.0.0 through 50.0.x changed none of these; 47
  requires OpenSSL 3.0+ (PyPI wheels bundle their own), 49 dropped x86_64 macOS wheels, 48 dropped
  Python 3.8. 50.0.0 is the first release fixing all four cryptography advisories. Ciphertext
  format is unchanged (12-byte nonce + AES-GCM output).
- OpenTelemetry family moved together: API, SDK and OTLP/HTTP exporter `1.45.1`;
  instrumentation packages `0.66b1`. `0.66b0` cannot co-resolve with SDK 1.45.1 because of the
  semantic-conventions pin, so `0.66b1` is required. The lock adds two new exporter helper
  packages (`opentelemetry-exporter-http-transport`, `opentelemetry-exporter-otlp-common`).
- `openapi-python-client==0.29.0` → `==0.29.1`.
- `pytest>=8.4.0,<9` → `>=9.0.3,<10`. The suite uses `--strict-config` and `--strict-markers`,
  which pytest 9 enforces; the markers are all declared. `pytest-cov 6.3` and `pytest-xdist 3.8`
  were kept and resolve against pytest 9.2.0. Whether they behave correctly is for CI to confirm.
- Locked-only bumps (no constraint change): certifi, dnspython, mako, urllib3.

## Review items for CI

1. Run the full unit, integration and proof suites; pytest 9 is the largest behavioural change.
2. Rebuild the Python images so certifi and cryptography wheels are refreshed.
3. Confirm OTel tracing and the exporter still start (new transport packages, new beta
   instrumentation line).
4. Regenerate the SDK with the 0.29.1 client only if the generated output is expected to be
   reproducible; this change did not regenerate it.

Next review date: 2026-11-10 (re-run `uv audit --frozen`).

## JavaScript installation policy

Both independent npm projects pin `npm@11.19.1` in `packageManager`. CI and the console Dockerfile
install that manager version explicitly. Their tracked `.npmrc` files disable dependency lifecycle
scripts; the documented, Makefile, CI and Docker installs also pass `--ignore-scripts` explicitly.
Browser installation remains an explicit Playwright command after the frozen install. No dependency
postinstall script is blanket-approved. The lockfiles are unchanged by this policy.

`npm audit --package-lock-only --ignore-scripts --omit=dev` returned zero production advisories
for both console and edge. The edge runtime has no npm production dependencies; this result does
not cover its development/deployment toolchain. Full graph scans and the reviewed moderate-only deferral are recorded in
[node-dependencies.md](node-dependencies.md). CI and `make dependency-audit` enforce the high/critical
threshold across the full graphs, including development dependencies.

The project, CI and Python image use uv0.12.19, enforced by `tool.uv.required-version`.
Run `make dependency-audit` against committed locks before release.
