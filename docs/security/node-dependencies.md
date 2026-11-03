# Node dependency advisories (console and edge)

Scope: `apps/console` and `apps/edge`. Source scans: native `npm audit --json`
including dev dependencies, run before and after the change. Production-only
scans (`npm audit --omit=dev`) were already clean and still are.

Review date for all deferrals below: **2026-11-10**.

## Result

| Package | Before (full graph) | After (full graph) |
|---|---|---|
| console | 19 advisories, 4 high | 15 moderate, 0 high |
| edge | 22 advisories, 9 high | 11 moderate, 0 high |

Every remaining finding is one moderate advisory (minimatch, GHSA-g3hv-75wg-m249)
and the ESLint packages that inherit it. See "Deferred".

Method: lockfile-only resolution (`npm install --package-lock-only
--ignore-scripts`, `npm update <name> --package-lock-only`), no `npm audit fix
--force`, no new `overrides`, no script approval. `.npmrc`
(`ignore-scripts=true`), scripts, `packageManager: npm@11.19.1` and runtime
dependencies are unchanged. `apps/console/package.json` is unchanged; only its
lockfile moved.

## Fixed

### Both apps (lockfile refresh within existing semver ranges)

| Package | Advisories | Version change | Reachability |
|---|---|---|---|
| brace-expansion (high) | GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p (recursion DoS), GHSA-q2hr-2g5m-vwhr (quadratic) | 1.1.18→1.1.21 (both apps); nested 5.0.9→5.0.12 (console), 2.1.4→2.1.7 (edge) | Dev only. Reached through minimatch in ESLint / typescript-eslint globbing of repo files. Patterns are repo-controlled, not attacker input. |
| js-yaml (high) | GHSA-2883-xcg3-v3hh (merge-key CPU use) | 4.3.1→4.3.2 | Dev only. Loaded by ESLint config loading of repo-controlled YAML/config. |

### console

| Package | Advisories | Version change | Reachability |
|---|---|---|---|
| browserslist (high) | GHSA-c83g-rgw3-j3cx, GHSA-73wf-gq98-2v4g | 4.28.6→4.29.5 (plus caniuse-lite, electron-to-chromium, node-releases, baseline-browser-mapping, update-browserslist-db refreshed with it) | Dev only (via eslint-config-next's Babel). Input is the repo's browserslist config. |
| zod (high) | GHSA-mrhx-c7xc-m3w5 (memory exhaustion on oversized constraints) | 4.4.3→4.6.7 | Dev only (lint toolchain). Not on the request path. |

### edge

| Package | Advisories | Version change | Reachability |
|---|---|---|---|
| wrangler (high, via miniflare) | miniflare range, carries sharp and undici advisories | 4.118.0→4.157.0 (direct dev dep, minor within 4.x) | Deploy and build tool (`npm run build` = `wrangler deploy --dry-run`). Local dev tooling and CI only, not shipped in the Worker bundle. |
| miniflare (high) | via sharp and undici | 5.20260730.0-alpha→5.20261103.0-beta (pulled in by wrangler 4.157.0, exact pin) | Same as wrangler. It is wrangler's local simulator. |
| undici (high) | Many advisories, 7 high (TLS cert validation bypass, cache poisoning, cross-user disclosure, WebSocket and decompression DoS and others), all fixed at or below 7.30.1 | 7.28.0→7.30.1 | Dev only, inside miniflare's local runtime. Worker code uses the platform `fetch`, not this package. Fixed rather than ignored because the tools run in CI and on developer machines. |
| sharp (high) | GHSA-g89c-p67h-r497, GHSA-rgj7-g3m4-5g8c, GHSA-wq5f-xc86-pv6w (libheif and librsvg) | 0.35.2→0.35.5 (with `@img/sharp-*` binaries and libvips 1.3.1→1.3.4) | Dev only, miniflare image handling. |
| vite (high, via launch-editor) | GHSA-587h-7wmj-h5pm and five more | 8.2.0→8.3.5 | Dev only. The vitest dev server is not exposed. |
| vitest and @vitest/mocker (moderate) | GHSA-82fw-gwwq-j7x9, GHSA-qxg6-89mg-rqv8 | 4.1.10→4.1.12 (direct dev dep, patch) | Dev only. |
| nanoid (high) | GHSA-2v37-7h3g-55p8 | 3.3.16→3.3.20 | Dev only, via postcss in the vite toolchain. |
| source-map-js (high) | GHSA-68fv-2mgg-jv7q | 1.2.1→1.2.2 | Dev only, via postcss. |
| @cloudflare/workers-types | none (peer constraint) | 5.20260801.1→5.20261103.1 | Required: wrangler 4.157.0 declares peer `^5.20261103.1`. Types only, so the type-check is where a regression would show. |

Collateral lock movement in edge from the wrangler and vitest bumps: esbuild
0.28.1→0.28.2, workerd 1.20260730.1→1.20261103.1, rolldown 1.2.1→1.2.16, postcss
8.5.25→8.5.32, chai, tslib, `@oxc-project/types` and the matching platform
binaries. The optional wasm32 fallback packages for rolldown and sharp
(`@napi-rs/wasm-runtime`, `@emnapi/*`, `@tybys/wasm-util`,
`@rolldown/binding-wasm32-wasi`) changed or dropped out of the lock because the
new releases no longer declare them. Node 24.12.0 (CI and `Dockerfile.console`)
satisfies the new engine requirements (wrangler `>=22`, vite `^20.19||>=22.12`).

## Deferred (review 2026-11-10)

| Advisory | Severity | Packages | Why deferred |
|---|---|---|---|
| GHSA-g3hv-75wg-m249 (minimatch globparts amplification DoS, vulnerable `<11.0.1`) | moderate | minimatch 3.1.5 (ESLint 9, eslint-plugin-*), minimatch 10.2.5 (console) and 9.0.9 (edge) under `@typescript-eslint/typescript-estree`. Npm also flags the packages that depend on them: eslint, `@eslint/config-array`, `@eslint/eslintrc`, `@eslint-community/eslint-utils`, `@typescript-eslint/*`, typescript-eslint, and in console eslint-config-next, eslint-plugin-import, eslint-plugin-jsx-a11y, eslint-plugin-react. | The only fixed line is minimatch 11, a major release with an export change that ESLint 9's 3.x consumers do not support. The "fix" npm offers is a major ESLint or eslint-config-next change (`fixAvailable` reports `eslint@4.0.0` / `eslint-config-next@0.2.4`, which are downgrades and nonsensical), so it was not applied. Forcing an override to 11 would risk breaking lint. **Mitigation:** dev-only; minimatch is called with repo-controlled glob patterns from ESLint config (`eslint .`, `eslint src test`), never attacker input, and the production graph scans clean. **Not claimed fixed.** Re-check on 2026-11-10 for an ESLint 9/10 or typescript-eslint release that adopts minimatch 11 or a patched backport. |

No high or critical advisory remains deferred.

## Validation and limits

Done: native `npm audit --json` (full graph) and `npm audit --omit=dev` on
the updated lockfiles with an empty environment, scratch HOME and cache and no
credential config; `npm ls --package-lock-only --all` is clean (no invalid or
missing entries) for both apps. Metadata resolution ran with
`--ignore-scripts --no-audit`.

Not done here, so it needs CI or the parent sandbox: `npm ci`, lint, type-check,
unit tests (vitest 4.1.12, wrangler 4.157.0 dry-run build) and the console build
were not run. Specific things to watch: the `@cloudflare/workers-types` and
wrangler bump changes the type surface used by `tsc --noEmit` in edge; the lock
was produced on macOS, so run `npm ci` on Linux to confirm that the removed
optional wasm entries do not matter; platform binaries (`@img/sharp-*`,
`@rolldown/*`, `@esbuild/*`) are present in the lock for all platforms.
