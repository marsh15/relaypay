"use client";

import Link from "next/link";
import { useState } from "react";
import type { ApiError, ScenarioResult } from "@/lib/types";

const expectedAssertions = [
  ["providerEffects", "One provider capture effect", 1],
  ["captures", "One local capture", 1],
  ["journals", "One balanced journal", 1],
  ["events", "One immutable event", 1],
  ["deliveries", "One acknowledged webhook", 1],
  ["attachedKeys", "Two keys share terminal bytes", 2],
] as const;

async function pollScenario(initial: ScenarioResult): Promise<ScenarioResult> {
  let current = initial;
  for (let attempt = 0; attempt < 8 && current.status === "RUNNING"; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, Math.min(4000, 500 * 2 ** attempt)));
    const response = await fetch(`/backend/api/demo/scenarios/${current.scenario_run_id}`);
    if (!response.ok) throw new Error("Scenario progress is temporarily unavailable.");
    current = (await response.json()) as ScenarioResult;
  }
  return current;
}

export function ScenarioLab({ csrfToken }: { csrfToken: string }) {
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runScenario() {
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch("/backend/api/demo/scenarios", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ scenarioType: "LOST_CAPTURE_RESPONSE" }),
      });
      if (!response.ok) {
        const payload = (await response.json()) as ApiError;
        throw new Error(payload.error?.message ?? "The scenario could not start.");
      }
      setResult(await pollScenario((await response.json()) as ScenarioResult));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The scenario could not complete.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="lab-grid">
      <section className="scenario-panel" aria-labelledby="scenario-title">
        <div className="scenario-index" aria-hidden="true">
          01
        </div>
        <div>
          <p className="section-kicker">Primary proof</p>
          <h2 id="scenario-title">Lost capture response</h2>
          <p>
            The provider commits one capture, then RelayPay receives no usable response. Recovery
            must query the stable key—never repeat the mutation—and finalize exactly once.
          </p>
        </div>
        <dl className="scenario-facts">
          <div>
            <dt>Injected fault</dt>
            <dd>One mutation response is lost</dd>
          </div>
          <div>
            <dt>Expected recovery</dt>
            <dd>Status-only provider lookup</dd>
          </div>
          <div>
            <dt>Financial claim</dt>
            <dd>One local effect and balanced journal</dd>
          </div>
        </dl>
        <button className="button button-primary" type="button" onClick={runScenario} disabled={pending}>
          {pending ? "Running proof…" : "Run lost-response scenario"}
        </button>
        <p className="synthetic-inline">Uses generated INR test data. No external payment rail.</p>
      </section>

      <section className="proof-panel" aria-labelledby="proof-title" aria-busy={pending}>
        <div className="proof-heading">
          <div>
            <p className="section-kicker">Observed evidence</p>
            <h2 id="proof-title">Run proof</h2>
          </div>
          <span className={`state-badge state-${result?.status.toLowerCase() ?? "idle"}`}>
            <span aria-hidden="true">{result?.status === "SUCCEEDED" ? "✓" : "○"}</span>
            {pending ? "In progress" : result?.status.replaceAll("_", " ") ?? "Ready"}
          </span>
        </div>
        {error ? (
          <div className="callout callout-danger" role="alert">
            <strong>Scenario needs inspection.</strong>
            <span>{error} Known committed state has not been labelled failed.</span>
          </div>
        ) : null}
        {!result && !pending ? (
          <div className="proof-empty">
            <span aria-hidden="true">↳</span>
            <p>Run the scenario to populate the causal timeline and invariant assertions.</p>
          </div>
        ) : null}
        {pending ? <ScenarioSkeleton /> : null}
        {result ? <ScenarioProof result={result} /> : null}
      </section>
    </div>
  );
}

function ScenarioSkeleton() {
  return (
    <div className="scenario-skeleton" role="status" aria-label="Running lost-response scenario">
      <p>Creating payment and injecting one lost response…</p>
      <div />
      <div />
      <div />
    </div>
  );
}

function ScenarioProof({ result }: { result: ScenarioResult }) {
  return (
    <div className="scenario-proof">
      <ol className="stepper" aria-label="Scenario progress">
        {result.steps.map((step) => (
          <li key={step.key}>
            <span className="step-marker" aria-hidden="true">
              ✓
            </span>
            <span>{step.label}</span>
          </li>
        ))}
      </ol>
      <div className="assertion-list" aria-label="Verified invariants">
        {expectedAssertions.map(([key, label, expected]) => {
          const matches = result.assertions[key] === expected;
          return (
            <div className={`assertion-row ${matches ? "" : "assertion-mismatch"}`} key={key}>
              <span className="assertion-icon" aria-hidden="true">
                {matches ? "✓" : "!"}
              </span>
              <span>{label}</span>
              <code>{String(result.assertions[key])}</code>
              <span className="sr-only">
                Observed {String(result.assertions[key])}; expected {expected}
              </span>
            </div>
          );
        })}
      </div>
      <dl className="correlation-strip">
        <div>
          <dt>Correlation</dt>
          <dd>{result.correlation_id}</dd>
        </div>
        <div>
          <dt>Event digest</dt>
          <dd>{result.assertions.eventSha256}</dd>
        </div>
      </dl>
      {result.payment_intent_id ? (
        <Link className="button button-primary" href={`/payments/${result.payment_intent_id}`}>
          Inspect payment evidence
        </Link>
      ) : null}
      <p className="sr-only" aria-live="polite">
        {result.status === "SUCCEEDED" ? "Lost-response scenario verified successfully" : ""}
      </p>
    </div>
  );
}
