import type { ReactNode } from "react";

export function StateBadge({ state }: { state: string }) {
  const normalized = state.toLowerCase();
  const success = ["succeeded", "delivered", "captured", "authorized", "refunded"].includes(
    normalized,
  );
  return (
    <span className={`state-badge state-${success ? "succeeded" : normalized}`}>
      <span aria-hidden="true">{success ? "✓" : normalized === "processing" ? "…" : "!"}</span>
      {state.replaceAll("_", " ")}
    </span>
  );
}

export function EvidenceSection({
  id,
  title,
  intro,
  children,
}: {
  id: string;
  title: string;
  intro: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="evidence-section" aria-labelledby={`${id}-title`}>
      <header className="evidence-section-heading">
        <h2 id={`${id}-title`}>{title}</h2>
        <p>{intro}</p>
      </header>
      {children}
    </section>
  );
}

export function Money({ paise }: { paise: number }) {
  return (
    <span className="money" aria-label={`${paise} paise, Indian rupees ${(paise / 100).toFixed(2)}`}>
      ₹{(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
    </span>
  );
}
