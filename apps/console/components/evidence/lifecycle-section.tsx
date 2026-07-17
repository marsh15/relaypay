import type { Evidence } from "@/lib/evidence-types";
import { EvidenceSection, Money, StateBadge } from "./shared";

export function LifecycleSection({ evidence }: { evidence: Evidence }) {
  const equation = evidence.paymentIntent.refundAvailability;
  return (
    <EvidenceSection
      id="lifecycle"
      title="Lifecycle and refundable value"
      intro="Durable resource states and the reservation equation used for refund authorization."
    >
      <ol className="timeline">
        <li>
          <span className="timeline-marker" aria-hidden="true" />
          <div>
            <strong>Payment intent created</strong>
            <time dateTime={evidence.paymentIntent.createdAt}>
              {new Date(evidence.paymentIntent.createdAt).toLocaleString("en-IN", {
                timeZone: "UTC",
              })}{" "}
              UTC
            </time>
          </div>
          <code>{evidence.paymentIntent.id}</code>
        </li>
        {evidence.resources.map((resource) => (
          <li key={resource.id}>
            <span className="timeline-marker" aria-hidden="true" />
            <div>
              <strong>{resource.type.toLowerCase()} state committed</strong>
              <StateBadge state={resource.status} />
            </div>
            <code>{resource.id}</code>
          </li>
        ))}
      </ol>
      <div className="refund-equation" aria-label="Refund availability equation">
        <div>
          <Money paise={equation.captured} />
          <span>captured</span>
        </div>
        <span aria-hidden="true">−</span>
        <div>
          <Money paise={equation.succeededRefunds} />
          <span>succeeded refunds</span>
        </div>
        <span aria-hidden="true">−</span>
        <div>
          <Money paise={equation.reservedRefunds} />
          <span>processing/review reserved</span>
        </div>
        <span aria-hidden="true">=</span>
        <div className="equation-result">
          <Money paise={equation.available} />
          <span>available</span>
        </div>
      </div>
    </EvidenceSection>
  );
}
