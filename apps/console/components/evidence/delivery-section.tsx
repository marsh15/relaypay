import type { Evidence } from "@/lib/evidence-types";
import { ReplayDeliveryButton } from "./replay-delivery-button";
import { EvidenceSection, StateBadge } from "./shared";

export function DeliverySection({ evidence, csrfToken }: { evidence: Evidence; csrfToken: string }) {
  return (
    <EvidenceSection
      id="delivery"
      title="Immutable event and delivery"
      intro="Captured endpoint versions, stable event digests, leased attempts, and receiver acknowledgements."
    >
      <div className="event-stack">
        {evidence.events.map((event) => {
          const recipient = evidence.recipients.find((item) => item.eventId === event.id);
          return (
            <article className="event-card" key={event.id}>
              <header>
                <div>
                  <p className="section-kicker">{event.type}</p>
                  <h3>{event.id}</h3>
                </div>
                <span className="immutability-label">Immutable bytes</span>
              </header>
              <dl className="fact-grid">
                <div>
                  <dt>Event SHA-256</dt>
                  <dd>{event.sha256}</dd>
                </div>
                <div>
                  <dt>Captured endpoint version</dt>
                  <dd>{recipient?.endpointVersionId ?? "Pending recipient snapshot"}</dd>
                </div>
              </dl>
            </article>
          );
        })}
      </div>
      <div className="delivery-list">
        {evidence.deliveries.map((delivery) => {
          const attempts = evidence.deliveryAttempts.filter(
            (attempt) => attempt.deliveryId === delivery.id,
          );
          return (
            <article key={delivery.id}>
              <header>
                <div>
                  <h3>{delivery.id}</h3>
                  <p>{delivery.attemptCount} delivery attempt(s)</p>
                </div>
                <StateBadge state={delivery.status} />
              </header>
              {attempts.length ? (
                <ol className="attempt-list" aria-label={`Delivery attempts for ${delivery.id}`}>
                  {attempts.map((attempt) => (
                    <li key={`${delivery.id}-${attempt.sequence}`}>
                      <span>Attempt {attempt.sequence}</span>
                      <strong>{attempt.result}</strong>
                      <code>{attempt.eventSha256}</code>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>Delivery row pending materialization or first attempt.</p>
              )}
              <ReplayDeliveryButton
                deliveryId={delivery.id}
                status={delivery.status}
                csrfToken={csrfToken}
              />
            </article>
          );
        })}
      </div>
    </EvidenceSection>
  );
}
