# Limitations and non-goals

- Synthetic INR data only; no card, bank, UPI, KYC, PII, settlement, fee, dispute, or chargeback
  processing.
- One deterministic mock provider and one exact allowlisted bundled receiver.
- Reconciliation accepts bounded CSV/JSON evidence from the synthetic payment provider only;
  connector-specific ingestion remains scheduled for M5.
- Mismatch resolution records operator notes and may link an existing compensating journal, but
  it never creates or changes payment, provider, or ledger outcomes.
- Single-capture and bounded evidence assumptions are deliberate demonstration constraints.
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
