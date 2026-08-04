import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Agent operations" };

type Environment = { id: string; name: string; type: string };
type WorkflowRun = {
  id: string;
  status: string;
  route: string;
  tokensUsed: number;
  tokenBudget: number;
  costUsedUsdMicros: number;
  createdAt: string;
};

export default async function AgentOperations({
  searchParams,
}: {
  searchParams: Promise<{ environment?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/agents");
  const query = await searchParams;
  const environmentResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentResponse.ok
    ? ((await environmentResponse.json()) as Environment[])
    : [];
  const environment = environments.find((item) => item.id === query.environment) ?? environments[0];
  const response = environment
    ? await backendFetch(`/api/admin/v1/environments/${environment.id}/workflow-runs`)
    : null;
  const runs = response?.ok ? ((await response.json()) as WorkflowRun[]) : [];

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Shared agent control plane</p>
            <h1>Agent operations</h1>
            <p>Inspect durable queues, run traces, approval waits, and dead-letter evidence.</p>
          </div>
          <form className="environment-picker" method="get">
            <label htmlFor="environment">Environment</label>
            <select id="environment" name="environment" defaultValue={environment?.id}>
              {environments.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.type}</option>)}
            </select>
            <button className="button button-secondary" type="submit">Apply scope</button>
          </form>
        </header>
        <section className="resource-panel" aria-labelledby="agent-queue-title">
          <div className="resource-heading">
            <div><p className="section-kicker">Reclaimable PostgreSQL queue</p><h2 id="agent-queue-title">Workflow runs</h2></div>
            <span className="record-count">{runs.length} runs</span>
          </div>
          {runs.length === 0 ? <div className="resource-empty" role="status"><h3>No workflow runs</h3><p>Triggered agent workflows will appear here with immutable trace evidence.</p></div> : null}
          {runs.length > 0 ? <div className="table-scroll" tabIndex={0} aria-label="Scrollable workflow runs"><table className="operations-table"><thead><tr><th scope="col">Run</th><th scope="col">State</th><th scope="col">Route</th><th scope="col">Token budget</th><th scope="col">Cost</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td><Link href={`/agents/${run.id}?environment=${environment?.id}`}>{run.id}</Link></td><td>{run.status.replaceAll("_", " ")}</td><td>{run.route}</td><td>{run.tokensUsed} / {run.tokenBudget}</td><td>{run.costUsedUsdMicros} µUSD</td></tr>)}</tbody></table></div> : null}
        </section>
      </main>
    </ConsoleShell>
  );
}
