"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type Action = {
  id: string;
  type: "PAYMENT_RETRY" | "MESSAGE";
  status: string;
  scheduledFor: string;
  due: boolean;
};

const terminalStates = new Set(["RECOVERED", "TERMINATED", "EXPIRED"]);

export function RecoveryActions({
  environmentId,
  caseId,
  csrfToken,
  status,
  actions,
}: {
  environmentId: string;
  caseId: string;
  csrfToken: string;
  status: string;
  actions: Action[];
}) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function command(path: string, body?: unknown) {
    setBusy(true);
    setMessage("");
    const response = await fetch(`/backend/api/admin/v1/environments/${environmentId}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = (await response.json().catch(() => ({}))) as { error?: { message?: string } };
    setBusy(false);
    setMessage(
      response.ok
        ? "Saved. Recovery evidence and remaining actions were updated atomically."
        : (value.error?.message ?? "Request failed."),
    );
    if (response.ok) router.refresh();
  }

  const dueAction = actions.find(
    (item) =>
      ["SCHEDULED", "AMBIGUOUS"].includes(item.status) && item.due,
  );
  const terminal = terminalStates.has(status);
  return (
    <section className="resource-panel" aria-labelledby="recovery-actions-title">
      <div className="resource-heading">
        <div>
          <p className="section-kicker">Policy and consent controlled</p>
          <h2 id="recovery-actions-title">Next action</h2>
        </div>
      </div>
      <div className="dispute-actions">
        {dueAction ? (
          <button
            className="button button-primary"
            disabled={busy || terminal}
            onClick={() => command(`/recovery-actions/${dueAction.id}/execute`)}
          >
            {dueAction.status === "AMBIGUOUS"
              ? "Recover by provider lookup"
              : `Execute ${dueAction.type.replaceAll("_", " ").toLowerCase()}`}
          </button>
        ) : (
          <p className="supporting-copy">
            {terminal ? "The case is terminal. Every remaining action is suppressed." : "No action is due yet."}
          </p>
        )}
        {!terminal ? (
          <button
            className="button button-secondary"
            disabled={busy}
            onClick={() =>
              command(`/recovery-cases/${caseId}/opt-outs`, {
                sourceEventId: `bev_${crypto.randomUUID().replaceAll("-", "")}`,
                channel: "ALL",
              })
            }
          >
            Record workflow-wide opt-out
          </button>
        ) : null}
      </div>
      {message ? (
        <p role="status" aria-live="polite">
          {message}
        </p>
      ) : null}
    </section>
  );
}
