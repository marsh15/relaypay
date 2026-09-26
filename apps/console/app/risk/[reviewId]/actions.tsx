"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const DISPOSITIONS = ["REJECT_ONBOARDING", "REQUEST_DOCUMENTS", "CLOSE_NO_ACTION"] as const;

export function RiskActions({
  environmentId,
  reviewId,
  csrfToken,
  escalationStatus,
}: {
  environmentId: string;
  reviewId: string;
  csrfToken: string;
  escalationStatus: string | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [note, setNote] = useState("");
  const [disposition, setDisposition] = useState<(typeof DISPOSITIONS)[number]>(
    "REQUEST_DOCUMENTS",
  );

  async function command(path: string, body: unknown) {
    setBusy(true);
    setMessage("");
    const response = await fetch(
      `/backend/api/admin/v1/environments/${environmentId}/risk-reviews/${reviewId}${path}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify(body),
      },
    );
    const value = (await response.json().catch(() => ({}))) as {
      error?: { message?: string };
    };
    setBusy(false);
    setMessage(
      response.ok
        ? "Recorded. Immutable evidence, findings, and scores are unchanged."
        : (value.error?.message ?? "Request failed."),
    );
    if (response.ok) router.refresh();
  }

  const open = escalationStatus === "OPEN";
  return (
    <section className="resource-panel" aria-labelledby="risk-actions-title">
      <div className="resource-heading">
        <div>
          <p className="section-kicker">Annotate and disposition only</p>
          <h2 id="risk-actions-title">Reviewer actions</h2>
        </div>
      </div>
      <div className="settlement-ask">
        <label htmlFor="risk-note">Annotation</label>
        <textarea
          id="risk-note"
          rows={2}
          maxLength={2000}
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
        <button
          type="button"
          className="button button-secondary"
          disabled={busy || note.trim().length === 0}
          onClick={() => {
            void command("/annotations", { note });
            setNote("");
          }}
        >
          Add annotation
        </button>
        {open ? (
          <>
            <label htmlFor="risk-disposition">Disposition</label>
            <select
              id="risk-disposition"
              value={disposition}
              onChange={(event) =>
                setDisposition(event.target.value as (typeof DISPOSITIONS)[number])
              }
            >
              {DISPOSITIONS.map((item) => (
                <option key={item} value={item}>
                  {item.replaceAll("_", " ").toLowerCase()}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="button button-primary"
              disabled={busy || note.trim().length === 0}
              onClick={() => {
                void command("/escalation/disposition", { disposition, note });
                setNote("");
              }}
            >
              Record disposition
            </button>
          </>
        ) : (
          <p className="supporting-copy">
            {escalationStatus === "DISPOSITIONED"
              ? "This escalation is dispositioned."
              : "This review is not escalated."}
          </p>
        )}
        {message ? (
          <p role="status" aria-live="polite">
            {message}
          </p>
        ) : null}
      </div>
    </section>
  );
}
