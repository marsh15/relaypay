import type { Evidence } from "@/lib/evidence-types";
import { Money, StateBadge } from "./shared";

export function ProofSummary({ evidence }: { evidence: Evidence }) {
  const capture = evidence.providerOperations.find((item) => item.kind === "CAPTURE");
  const captureEvents = evidence.events.filter((item) => item.type === "payment.captured.v1");
  const captureEventIds = new Set(captureEvents.map((item) => item.id));
  const delivered = evidence.deliveries.filter(
    (item) => captureEventIds.has(item.eventId) && item.status === "DELIVERED",
  ).length;
  const captureKeys = evidence.idempotency.filter(
    (item) => item.fingerprintSummary.route_template === "/payment_intents/{payment_intent_id}/capture",
  ).length;
  return (
    <section className="proof-summary" aria-labelledby="proof-summary-title">
      <div className="proof-summary-lead">
        <p className="section-kicker">Proof summary</p>
        <h2 id="proof-summary-title">
          {capture?.status === "SUCCEEDED" ? "Verified: one provider capture effect" : "Payment evidence"}
        </h2>
        <div className="proof-summary-state">
          <StateBadge state={evidence.paymentIntent.status} />
          <Money paise={evidence.paymentIntent.amount} />
        </div>
      </div>
      <ul className="proof-metrics" aria-label="Evidence counts">
        <li>
          <strong>{evidence.resources.filter((item) => item.type === "CAPTURE").length}</strong>
          <span>capture</span>
        </li>
        <li>
          <strong>{evidence.ledger.journals.length}</strong>
          <span>balanced journal</span>
        </li>
        <li>
          <strong>{captureEvents.length}</strong>
          <span>capture event</span>
        </li>
        <li>
          <strong>{delivered}</strong>
          <span>delivered webhook</span>
        </li>
        <li>
          <strong>{captureKeys}</strong>
          <span>capture keys</span>
        </li>
      </ul>
    </section>
  );
}
