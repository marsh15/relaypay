import type { Evidence } from "@/lib/evidence-types";
import { RetryLookupButton } from "./retry-lookup-button";
import { EvidenceSection, StateBadge } from "./shared";

export function ProviderSection({ evidence, csrfToken }: { evidence: Evidence; csrfToken: string }) {
  return (
    <EvidenceSection
      id="provider"
      title="Provider send and recovery"
      intro="Canonical request digests, status-only lookups, classifications, and review history."
    >
      <div className="operation-stack">
        {evidence.providerOperations.map((operation) => {
          const attempts = evidence.providerAttempts.filter(
            (attempt) => attempt.operationId === operation.id,
          );
          const history = evidence.operationHistory.filter((item) => item.operationId === operation.id);
          return (
            <article className="operation-card" key={operation.id}>
              <header>
                <div>
                  <p className="section-kicker">{operation.kind}</p>
                  <h3>{operation.id}</h3>
                </div>
                <StateBadge state={operation.status} />
              </header>
              <dl className="fact-grid">
                <div>
                  <dt>Stable provider key</dt>
                  <dd>{operation.stableProviderKey}</dd>
                </div>
                <div>
                  <dt>Mutation attempts</dt>
                  <dd>{operation.attemptCount}</dd>
                </div>
                <div>
                  <dt>Request SHA-256</dt>
                  <dd>{operation.requestSha256 ?? "Not sent"}</dd>
                </div>
                <div>
                  <dt>Terminal response SHA-256</dt>
                  <dd>{operation.responseSha256 ?? "Outcome not yet known"}</dd>
                </div>
              </dl>
              {operation.status === "REQUIRES_REVIEW" ? (
                <RetryLookupButton operationId={operation.id} csrfToken={csrfToken} />
              ) : null}
              {attempts.length ? (
                <ol className="attempt-list" aria-label={`${operation.kind} provider attempts`}>
                  {attempts.map((attempt) => (
                    <li key={`${attempt.operationId}-${attempt.sequence}`}>
                      <span>{attempt.kind}</span>
                      <strong>{attempt.classification ?? attempt.state}</strong>
                      <code>{attempt.responseSha256 ?? attempt.safeErrorCode ?? "No response bytes"}</code>
                    </li>
                  ))}
                </ol>
              ) : null}
              {history.length ? (
                <details>
                  <summary>Recovery and transition history ({history.length})</summary>
                  <ul className="history-list">
                    {history.map((item, index) => (
                      <li key={`${item.correlationId}-${index}`}>
                        <code>{item.correlationId}</code>
                        <span>
                          {item.from ?? "NEW"} → {item.to} · {item.reason} · {item.actor}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </article>
          );
        })}
      </div>
    </EvidenceSection>
  );
}
