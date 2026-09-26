"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const SITES = [
  "COMPLETE_CLEAN",
  "MISSING_POLICIES",
  "YOUNG_DOMAIN",
  "PRICE_OUTLIER",
  "SUSPICIOUS_CLAIMS",
  "PROHIBITED_CATEGORY",
];

export function RiskReviewLauncher({
  environmentId,
  csrfToken,
}: {
  environmentId: string;
  csrfToken: string;
}) {
  const router = useRouter();
  const [siteRef, setSiteRef] = useState(SITES[0]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function start() {
    setBusy(true);
    setMessage("");
    const response = await fetch(
      `/backend/api/admin/v1/environments/${environmentId}/risk-reviews`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ siteRef }),
      },
    );
    const value = (await response.json().catch(() => ({}))) as {
      id?: string;
      error?: { message?: string };
    };
    setBusy(false);
    if (!response.ok || !value.id) {
      setMessage(value.error?.message ?? "Request failed.");
      return;
    }
    router.push(`/risk/${value.id}?environment=${environmentId}`);
  }

  return (
    <section className="resource-panel" aria-labelledby="risk-launcher-title">
      <div className="resource-heading">
        <div>
          <p className="section-kicker">Synthetic sites only</p>
          <h2 id="risk-launcher-title">Start a review</h2>
        </div>
      </div>
      <div className="settlement-ask">
        <label htmlFor="risk-site">Merchant site snapshot</label>
        <select
          id="risk-site"
          value={siteRef}
          onChange={(event) => setSiteRef(event.target.value as (typeof SITES)[number])}
        >
          {SITES.map((site) => (
            <option key={site} value={site}>
              {site.replaceAll("_", " ").toLowerCase()}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="button button-primary"
          disabled={busy}
          onClick={() => void start()}
        >
          {busy ? "Reviewing…" : "Run risk review"}
        </button>
        {message ? (
          <p role="status" aria-live="polite">
            {message}
          </p>
        ) : null}
      </div>
    </section>
  );
}
