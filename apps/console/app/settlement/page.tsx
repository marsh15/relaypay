import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { backendFetch, getSession } from "@/lib/server-api";
import { SettlementAsk } from "./ask";

export const metadata: Metadata = { title: "Settlement intelligence" };

type Environment = { id: string; name: string; type: string };
type Policy = {
  id: string;
  merchantAccountId: string | null;
  version: number;
  timezone: string;
  cutoff: string;
  settlementDelayDays: number;
  weekendHandling: string;
  status: string;
};
type Forecast = {
  id: string;
  merchantAccountId: string | null;
  businessDate: string;
  sequence: number;
  expectedSettlementAmount: number;
  expectedSettlementFormatted: string;
  expectedArrivalDate: string;
  snapshotSha256: string;
};
type Question = {
  id: string;
  question: string;
  intent: string;
  status: string;
  askedAt: string;
};

export default async function SettlementPage({
  searchParams,
}: {
  searchParams: Promise<{ environment?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/settlement");
  const query = await searchParams;
  const environmentResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentResponse.ok
    ? ((await environmentResponse.json()) as Environment[])
    : [];
  const environment =
    environments.find((item) => item.id === query.environment) ?? environments[0];
  const base = environment
    ? `/api/admin/v1/environments/${environment.id}`
    : null;
  const [policiesResponse, forecastsResponse, questionsResponse] = await Promise.all([
    base ? backendFetch(`${base}/settlement-policies`) : null,
    base ? backendFetch(`${base}/settlement-forecasts`) : null,
    base ? backendFetch(`${base}/settlement-questions`) : null,
  ]);
  const policies = policiesResponse?.ok
    ? ((await policiesResponse.json()) as Policy[])
    : [];
  const forecasts = forecastsResponse?.ok
    ? ((await forecastsResponse.json()) as Forecast[])
    : [];
  const questions = questionsResponse?.ok
    ? ((await questionsResponse.json()) as Question[])
    : [];
  const activePolicy = policies.find((item) => item.status === "ACTIVE");

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Deterministic answers, cited records</p>
            <h1>Settlement intelligence</h1>
            <p>
              Ask one of four supported questions; every number comes from fixed
              tenant-scoped queries with immutable citations.
            </p>
          </div>
          <form className="environment-picker" method="get">
            <label htmlFor="environment">Environment</label>
            <select id="environment" name="environment" defaultValue={environment?.id}>
              {environments.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.type}
                </option>
              ))}
            </select>
            <button className="button button-secondary" type="submit">
              Apply scope
            </button>
          </form>
        </header>
        {environment ? (
          <SettlementAsk environmentId={environment.id} csrfToken={session.csrfToken} />
        ) : null}
        <section className="resource-panel" aria-labelledby="settlement-policy-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Immutable, versioned</p>
              <h2 id="settlement-policy-title">Settlement policy</h2>
            </div>
          </div>
          {activePolicy ? (
            <p className="supporting-copy">
              {`${activePolicy.timezone} · cutoff ${activePolicy.cutoff} · T+${activePolicy.settlementDelayDays} · weekends ${
                activePolicy.weekendHandling === "SKIP" ? "skipped" : "included"
              } · version ${activePolicy.version}`}
            </p>
          ) : (
            <div className="resource-empty" role="status">
              <h3>No active policy</h3>
              <p>The default policy is created with the first forecast or question.</p>
            </div>
          )}
        </section>
        <section className="resource-panel" aria-labelledby="settlement-forecast-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Pre-cutoff snapshots</p>
              <h2 id="settlement-forecast-title">Forecasts</h2>
            </div>
            <span className="record-count">{forecasts.length} forecasts</span>
          </div>
          {forecasts.length === 0 ? (
            <div className="resource-empty" role="status">
              <h3>No forecasts yet</h3>
              <p>The worker records one immutable forecast per business date.</p>
            </div>
          ) : (
            <div className="table-scroll" tabIndex={0} aria-label="Scrollable forecasts">
              <table className="operations-table">
                <thead>
                  <tr>
                    <th scope="col">Forecast</th>
                    <th scope="col">Business date</th>
                    <th scope="col">Expected</th>
                    <th scope="col">Arrival</th>
                    <th scope="col">Snapshot digest</th>
                  </tr>
                </thead>
                <tbody>
                  {forecasts.map((item) => (
                    <tr key={item.id}>
                      <td>{item.id}</td>
                      <td>{item.businessDate}</td>
                      <td>{item.expectedSettlementFormatted}</td>
                      <td>{item.expectedArrivalDate}</td>
                      <td>{item.snapshotSha256.slice(0, 16)}…</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="resource-panel" aria-labelledby="settlement-questions-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Idempotent, fully cited</p>
              <h2 id="settlement-questions-title">Questions</h2>
            </div>
            <span className="record-count">{questions.length} questions</span>
          </div>
          {questions.length === 0 ? (
            <div className="resource-empty" role="status">
              <h3>No questions yet</h3>
              <p>Ask one of the four supported questions above.</p>
            </div>
          ) : (
            <div className="table-scroll" tabIndex={0} aria-label="Scrollable questions">
              <table className="operations-table">
                <thead>
                  <tr>
                    <th scope="col">Question</th>
                    <th scope="col">Intent</th>
                    <th scope="col">State</th>
                    <th scope="col">Asked</th>
                  </tr>
                </thead>
                <tbody>
                  {questions.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <Link
                          href={`/settlement/${item.id}?environment=${environment?.id}`}
                        >
                          {item.question.length > 64
                            ? `${item.question.slice(0, 64)}…`
                            : item.question}
                        </Link>
                      </td>
                      <td>{item.intent.replaceAll("_", " ")}</td>
                      <td>{item.status.replaceAll("_", " ").toLowerCase()}</td>
                      <td>{new Date(item.askedAt).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </ConsoleShell>
  );
}
