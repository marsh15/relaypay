import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Dispute response" };

type Environment = { id: string; name: string; type: string };
type Dispute = {
  id: string; networkDisputeId: string; reasonCode: string; amount: number;
  currency: string; status: string; dueAt: string;
};

export default async function DisputesPage({ searchParams }: { searchParams: Promise<{ environment?: string }> }) {
  const session = await getSession();
  if (!session) redirect("/login?next=/disputes");
  const query = await searchParams;
  const environmentResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentResponse.ok ? (await environmentResponse.json()) as Environment[] : [];
  const environment = environments.find((item) => item.id === query.environment) ?? environments[0];
  const response = environment ? await backendFetch(`/api/admin/v1/environments/${environment.id}/disputes`) : null;
  const disputes = response?.ok ? (await response.json()) as Dispute[] : [];
  return <ConsoleShell session={session}><main id="main-content" className="page page-operations">
    <header className="operations-header"><div><p className="eyebrow">Human-controlled revenue operations</p><h1>Dispute response</h1><p>Review evidence, revise drafts, approve exact package bytes, and submit once.</p></div>
      <form className="environment-picker" method="get"><label htmlFor="environment">Environment</label><select id="environment" name="environment" defaultValue={environment?.id}>{environments.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.type}</option>)}</select><button className="button button-secondary" type="submit">Apply scope</button></form>
    </header>
    <section className="resource-panel" aria-labelledby="dispute-queue-title"><div className="resource-heading"><div><p className="section-kicker">Deadline ordered review queue</p><h2 id="dispute-queue-title">Cases</h2></div><span className="record-count">{disputes.length} cases</span></div>
      {disputes.length === 0 ? <div className="resource-empty" role="status"><h3>No dispute cases</h3><p>Synthetic dispute.created.v1 events will appear here.</p></div> : <div className="table-scroll" tabIndex={0} aria-label="Scrollable dispute cases"><table className="operations-table"><thead><tr><th scope="col">Case</th><th scope="col">Reason</th><th scope="col">Amount</th><th scope="col">State</th><th scope="col">Due</th></tr></thead><tbody>{disputes.map((item) => <tr key={item.id}><td><Link href={`/disputes/${item.id}?environment=${environment?.id}`}>{item.networkDisputeId}</Link><br/><small>{item.id}</small></td><td>{item.reasonCode.replaceAll("_", " ")}</td><td>{item.currency} {(item.amount / 100).toFixed(2)}</td><td>{item.status.replaceAll("_", " ")}</td><td>{new Date(item.dueAt).toLocaleDateString()}</td></tr>)}</tbody></table></div>}
    </section>
  </main></ConsoleShell>;
}
