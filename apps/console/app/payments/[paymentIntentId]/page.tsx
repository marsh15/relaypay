import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { DeliverySection } from "@/components/evidence/delivery-section";
import { IdempotencySection } from "@/components/evidence/idempotency-section";
import { LedgerSection } from "@/components/evidence/ledger-section";
import { LifecycleSection } from "@/components/evidence/lifecycle-section";
import { ProofSummary } from "@/components/evidence/proof-summary";
import { ProviderSection } from "@/components/evidence/provider-section";
import { SafeJson } from "@/components/evidence/safe-json";
import type { Evidence } from "@/lib/evidence-types";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Payment evidence" };

const sections = [
  ["lifecycle", "Lifecycle"],
  ["idempotency", "Idempotency"],
  ["provider", "Provider evidence"],
  ["ledger", "Ledger"],
  ["delivery", "Events and delivery"],
] as const;

export default async function PaymentEvidencePage({
  params,
}: {
  params: Promise<{ paymentIntentId: string }>;
}) {
  const { paymentIntentId } = await params;
  const session = await getSession();
  if (!session) redirect(`/login?next=/payments/${encodeURIComponent(paymentIntentId)}`);
  const response = await backendFetch(`/api/v1/payment_intents/${paymentIntentId}/evidence`);
  if (response.status === 404) notFound();
  if (!response.ok) throw new Error("Payment evidence is temporarily unavailable.");
  const evidence = (await response.json()) as Evidence;
  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-evidence">
        <header className="evidence-page-header">
          <div>
            <Link className="back-link" href="/lab">
              ← Scenario Lab
            </Link>
            <p className="eyebrow">Payment evidence</p>
            <h1>Trace the committed proof</h1>
          </div>
          <dl>
            <div>
              <dt>Payment intent</dt>
              <dd>{evidence.paymentIntent.id}</dd>
            </div>
            <div>
              <dt>Merchant reference</dt>
              <dd>{evidence.paymentIntent.merchantReference}</dd>
            </div>
          </dl>
        </header>
        <ProofSummary evidence={evidence} />
        <details className="mobile-contents">
          <summary>Evidence sections</summary>
          <EvidenceLinks />
        </details>
        <div className="evidence-layout">
          <aside className="contents-rail" aria-label="Evidence sections">
            <p>On this page</p>
            <EvidenceLinks />
          </aside>
          <div className="evidence-column">
            <LifecycleSection evidence={evidence} />
            <IdempotencySection evidence={evidence} />
            <ProviderSection evidence={evidence} csrfToken={session.csrfToken} />
            <LedgerSection evidence={evidence} />
            <DeliverySection evidence={evidence} csrfToken={session.csrfToken} />
            <SafeJson value={evidence} />
          </div>
        </div>
      </main>
    </ConsoleShell>
  );
}

function EvidenceLinks() {
  return (
    <ol>
      {sections.map(([id, label]) => (
        <li key={id}>
          <a href={`#${id}`}>{label}</a>
        </li>
      ))}
    </ol>
  );
}
