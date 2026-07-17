import type { Evidence } from "@/lib/evidence-types";
import { Money, StateBadge } from "./shared";

export function ProofSummary({ evidence }: { evidence: Evidence }) {
  const capture = evidence.providerOperations.find((item) => item.kind === "CAPTURE");
  const delivered = evidence.deliveries.filter((item) => item.status === "DELIVERED").length;
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
          <strong>{evidence.events.length}</strong>
          <span>immutable event</span>
        </li>
        <li>
          <strong>{delivered}</strong>
          <span>delivered webhook</span>
        </li>
        <li>
          <strong>{evidence.idempotency.length}</strong>
          <span>attached keys</span>
        </li>
      </ul>
    </section>
  );
}
