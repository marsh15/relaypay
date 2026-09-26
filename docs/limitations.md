# Limitations and non-goals

- Synthetic INR data only; no card, bank, UPI, KYC, PII, real settlement, fee, dispute, or
  chargeback processing.
- One deterministic mock provider and one exact allowlisted bundled receiver.
- Reconciliation accepts bounded CSV/JSON evidence from the synthetic payment provider only;
  connector-specific ingestion remains scheduled for M5.
- Mismatch resolution records operator notes and may link an existing compensating journal, but
  it never creates or changes payment, provider, or ledger outcomes.
- Single-capture and bounded evidence assumptions are deliberate demonstration constraints.
- Merchant settlement and payout administration are API-first. The synthetic bank is a
  deterministic test boundary, not a real transfer rail.
- Connector and commerce integrations are deterministic synthetic boundaries. Commerce excludes
  inventory, fulfillment, taxes, distributed sagas, and real merchant credentials.
- No multi-region operation, automatic failover, point-in-time recovery, managed KMS, WAF, or
  production observability backend.
- In-process login rate limiting is per API process; a distributed limiter is required before
  horizontal public deployment.
- The Compose deployment is an optional single-host Ubuntu LTS sandbox, not a PCI-DSS or
  regulated production architecture.
- Caddy terminates HTTPS, but host firewalling, OS patching, DNS, monitoring, restore drills, and
  secret rotation remain operator responsibilities.
- The reset operation is coordinated but not atomic across the three database ownership domains;
  application workers must be stopped, as documented.
- Repository publication, hosted deployment, and demo-video recording require owner-controlled
  external accounts and are intentionally not automated by this codebase.
- Portfolio analytics aggregates are organisation+environment scoped queries computed on demand;
  there is no warehouse, materialized view, incremental rollup, or long-term metric history beyond
  the current Prometheus gauge values.
- Handling-time baselines and analytics shapes are versioned constants in code, not operator
  configuration; changing them is a code change, not a setting.
- The live-provider comparison report requires vendor API keys, calls external services, and is
  intentionally excluded from CI; without keys the script is inert and the deterministic
  evaluation runner remains the only release quality gate.
- The optional edge origin boundary (opt-in via `EDGE_ORIGIN_SIGNATURE_REQUIRED`) verifies the
  HMAC, timestamp window, and nonce format of each request, but the origin does not keep a replay
  store: a captured signed request can be replayed within the timestamp window unless the origin is
  reachable only through the edge worker, which does track replay keys. Production deployments must
  network-restrict the origin to the worker.
- The versioned evaluation fixtures pin current deterministic behaviour; regenerating them is a
  deliberate, reviewed change (via the checked-in generator), not an automated step.
