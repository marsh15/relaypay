import type { Evidence } from "@/lib/evidence-types";
import { EvidenceSection } from "./shared";

export function IdempotencySection({ evidence }: { evidence: Evidence }) {
  const digests = new Set(evidence.idempotency.map((item) => item.responseSha256));
  return (
    <EvidenceSection
      id="idempotency"
      title="Idempotency attachments"
      intro="Redacted key hints and route-aware fingerprints bound to canonical terminal bytes."
    >
      <div className="invariant-banner">
        <span aria-hidden="true">✓</span>
        <strong>Terminal response digests are {digests.size === 1 ? "byte-identical" : "different"}.</strong>
        <code>{[...digests][0] ?? "Pending terminal evidence"}</code>
      </div>
      <div className="table-scroll" tabIndex={0} aria-label="Scrollable idempotency evidence table">
        <table>
          <caption>Bound idempotency records, limited to {evidence.limits.perCollection}</caption>
          <thead>
            <tr>
              <th scope="col">Key hint</th>
              <th scope="col">Route</th>
              <th scope="col">Terminal</th>
              <th scope="col">Response digest</th>
            </tr>
          </thead>
          <tbody>
            {evidence.idempotency.map((item, index) => (
              <tr key={`${item.keyHint}-${index}`}>
                <td>
                  <code>{item.keyHint ?? "redacted"}</code>
                </td>
                <td>
                  <code>{String(item.fingerprintSummary.route_template ?? "unknown")}</code>
                </td>
                <td>{item.isTerminal ? "Yes" : "No"}</td>
                <td>
                  <code>{item.responseSha256 ?? "Pending"}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </EvidenceSection>
  );
}
