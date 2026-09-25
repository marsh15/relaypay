"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const EXAMPLES = [
  "Why is today's settlement lower than expected?",
  "Which payments are still unsettled?",
  "What is the refund cash impact today?",
  "When is the expected arrival tomorrow?",
];

export function SettlementAsk({
  environmentId,
  csrfToken,
}: {
  environmentId: string;
  csrfToken: string;
}) {
  const router = useRouter();
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function ask(text: string) {
    setBusy(true);
    setMessage("");
    const response = await fetch(
      `/backend/api/admin/v1/environments/${environmentId}/settlement-questions`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ question: text }),
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
    router.push(`/settlement/${value.id}?environment=${environmentId}`);
  }

  return (
    <section className="resource-panel" aria-labelledby="settlement-ask-title">
      <div className="resource-heading">
        <div>
          <p className="section-kicker">Four supported intents</p>
          <h2 id="settlement-ask-title">Ask the settlement agent</h2>
        </div>
      </div>
      <div className="settlement-ask">
        <label htmlFor="settlement-question">Question</label>
        <textarea
          id="settlement-question"
          rows={2}
          maxLength={2000}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <div className="settlement-examples">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className="button button-secondary"
              disabled={busy}
              onClick={() => {
                setQuestion(example);
                void ask(example);
              }}
            >
              {example}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="button button-primary"
          disabled={busy || question.trim().length === 0}
          onClick={() => void ask(question)}
        >
          {busy ? "Answering…" : "Ask"}
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
