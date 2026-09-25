import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { SafeJson } from "@/components/evidence/safe-json";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Settlement answer" };

type Citation = {
  recordType: string;
  recordId: string;
  fieldPaths: string[];
  snapshotSha256: string;
};
type Answer = {
  intent: string;
  numbers: Record<string, number>;
  dates: Record<string, string>;
  calculationSteps: string[];
  citations: Citation[];
  trace: string[];
  narrative: string;
  clarification?: { message: string; supportedIntents: string[] };
};
type Question = {
  id: string;
  question: string;
  intent: string;
  status: string;
  classification: Record<string, unknown>;
  answer: Answer | null;
  answerSha256: string | null;
  askedAt: string;
  answeredAt: string | null;
};

export default async function SettlementQuestionPage({
  params,
  searchParams,
}: {
  params: Promise<{ questionId: string }>;
  searchParams: Promise<{ environment?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/settlement");
  const [{ questionId }, query] = await Promise.all([params, searchParams]);
  if (!query.environment) notFound();
  const response = await backendFetch(
    `/api/admin/v1/environments/${query.environment}/settlement-questions/${questionId}`,
  );
  if (!response.ok) notFound();
  const item = (await response.json()) as Question;
  const answer = item.answer;

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Deterministic settlement answer</p>
            <h1>{item.question}</h1>
            <p>
              {item.intent.replaceAll("_", " ")} · {item.status.replaceAll("_", " ").toLowerCase()}{" "}
              · asked {new Date(item.askedAt).toLocaleString()}
            </p>
          </div>
        </header>
        {answer ? (
          <>
            <section className="resource-panel" aria-labelledby="answer-narrative-title">
              <div className="resource-heading">
                <div>
                  <p className="section-kicker">Validated against the deterministic result</p>
                  <h2 id="answer-narrative-title">Narrative</h2>
                </div>
              </div>
              <p>{answer.narrative}</p>
            </section>
            {answer.clarification ? (
              <section className="resource-panel" aria-labelledby="answer-clarify-title">
                <div className="resource-heading">
                  <div>
                    <p className="section-kicker">Typed clarification</p>
                    <h2 id="answer-clarify-title">Rephrase the question</h2>
                  </div>
                </div>
                <p>{answer.clarification.message}</p>
                <ul>
                  {answer.clarification.supportedIntents.map((intent) => (
                    <li key={intent}>{intent.replaceAll("_", " ").toLowerCase()}</li>
                  ))}
                </ul>
              </section>
            ) : null}
            {Object.keys(answer.numbers).length > 0 ? (
              <section className="resource-panel" aria-labelledby="answer-numbers-title">
                <div className="resource-heading">
                  <div>
                    <p className="section-kicker">Paise and counts</p>
                    <h2 id="answer-numbers-title">Numbers</h2>
                  </div>
                </div>
                <div className="table-scroll" tabIndex={0} aria-label="Answer numbers">
                  <table className="operations-table">
                    <thead>
                      <tr>
                        <th scope="col">Field</th>
                        <th scope="col">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(answer.numbers).map(([field, value]) => (
                        <tr key={field}>
                          <td>{field}</td>
                          <td>{value.toLocaleString("en-IN")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}
            {answer.calculationSteps.length > 0 ? (
              <section className="resource-panel" aria-labelledby="answer-steps-title">
                <div className="resource-heading">
                  <div>
                    <p className="section-kicker">Reproducible</p>
                    <h2 id="answer-steps-title">Calculation steps</h2>
                  </div>
                </div>
                <ol>
                  {answer.calculationSteps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              </section>
            ) : null}
            {answer.citations.length > 0 ? (
              <section className="resource-panel" aria-labelledby="answer-citations-title">
                <div className="resource-heading">
                  <div>
                    <p className="section-kicker">recordType · recordId · fieldPaths · digest</p>
                    <h2 id="answer-citations-title">Citations</h2>
                  </div>
                  <span className="record-count">{answer.citations.length} citations</span>
                </div>
                <div className="table-scroll" tabIndex={0} aria-label="Answer citations">
                  <table className="operations-table">
                    <thead>
                      <tr>
                        <th scope="col">Record</th>
                        <th scope="col">Type</th>
                        <th scope="col">Field paths</th>
                        <th scope="col">Snapshot digest</th>
                      </tr>
                    </thead>
                    <tbody>
                      {answer.citations.map((citation) => (
                        <tr key={`${citation.recordType}-${citation.recordId}`}>
                          <td>{citation.recordId}</td>
                          <td>{citation.recordType.replaceAll("_", " ")}</td>
                          <td>{citation.fieldPaths.join(", ")}</td>
                          <td>{citation.snapshotSha256.slice(0, 16)}…</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}
          </>
        ) : (
          <section className="resource-panel" aria-labelledby="answer-pending-title">
            <div className="resource-heading">
              <div>
                <p className="section-kicker">Not yet finalized</p>
                <h2 id="answer-pending-title">No answer recorded</h2>
              </div>
            </div>
            <p>This question has not produced an answer yet.</p>
          </section>
        )}
        <section className="resource-panel" aria-labelledby="answer-evidence-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Immutable classification evidence</p>
              <h2 id="answer-evidence-title">Classification</h2>
            </div>
          </div>
          <SafeJson value={item.classification} />
        </section>
      </main>
    </ConsoleShell>
  );
}
