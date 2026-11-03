# RelayPay security audit — 2026-11-03

Standard, repository-wide source audit of commit `41f3774439f642638dcf662bc8dab6a6f64326b7`, with the pre-existing portfolio-demo working-tree edit included in the immutable snapshot. This is a synthetic payment sandbox, not a real-money processor.

The review retained **0 confirmed vulnerabilities, 24 needs-validation records, and 1 rejected claim**. Needs-validation records have no severity: their specific actor, deployment, runtime or impact prerequisites remain unresolved. This result is not a guarantee of security or proof that a deployment is safe.

## Coverage and method

- 866 tracked snapshot files; 217 distinct source paths named in checks across 151 deterministic coverage units. This does not claim line-by-line review of every snapshot file.
- Unit dispositions: 119 covered, 31 linked to candidate records, 1 blocked. All candidate fingerprints received independent validation; all retained needs-validation records received a separate final-record review.
- Six hunter waves, immediate post-wave critics, and a distinct final-clean critic. Final critic reported no missing units or reassignments. Prior run records and relevant source bytes were compared; no prior result implied blanket coverage.
- Target execution used private, resource-limited, network-disabled Docker fixtures with read-only source, allowlisted environment and parent-promoted bounded outputs. No deployed endpoint, shared database, real provider or real credential was tested.
- Both dependency-free finding and coverage validators pass. Full canonical records and ledger are retained alongside this report; the sanitized source manifest fixes the reviewed bytes.
- Provider rate limits and malformed reviewer responses caused discarded attempts and fresh assignments. Runtime expiry and connector-identity reviews were rejected by the provider safety filter before execution; only narrowed source review followed. Their records explicitly preserve the absence of reproduction.
- One ingress unit remains blocked on effective Caddy/Uvicorn forwarded-header behavior and owner-observed topology. Docker unavailability during that assignment is historical; Docker was later restored, but no ingress-chain fixture was run.

## Interpretation

Most retained concerns depend on optional edge/observability deployment or control of synthetic snapshot/provider inputs that the shipped fixture flow does not establish. Small mocks establish call order or state allocation, not availability degradation, Cloudflare platform semantics, external exposure or financial duplication. Preserving those limits is part of the result.

The recurring-failure replay disclosure claim was rejected because the shipped HTTP producer passes an ISO string to a strict datetime field and fails before intake. That functional defect and the latent unscoped service lookup should be fixed together; restoring the endpoint alone would change reachability.

The four provenance-reserved implementation groups remain maintainer work. This audit does not implement or certify their missing decisions.

## Retained outputs

- `findings.json`: all25 independently dispositioned records, including the rejected claim.
- `coverage-ledger.json`: full coverage, check ownership, blockers and validation decisions.
- `source-manifest.json`: exact snapshot path hashes and byte lengths.
- `NEEDS-VALIDATION.md`: conditional records and safe owner-observation plans.
- `FINDINGS-DETAIL.md`: no confirmed vulnerabilities; rejected-claim appendix.

## Remediation status

This report describes the baseline snapshot. Prepared patches were tested only in disposable copies before baseline closure. Subsequent repository hardening and its verification must be recorded separately, without relabeling unproven baseline concerns as confirmed.
