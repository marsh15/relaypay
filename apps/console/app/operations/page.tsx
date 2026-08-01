import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { SafeJson } from "@/components/evidence/safe-json";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Operations workspace" };

type Environment = { id: string; name: string; type: string; status: string };
type OperationsPage = { data: Array<Record<string, unknown>>; nextCursor: string | null };

const resources = [
  ["merchant-accounts", "Merchant accounts"],
  ["settlements", "Settlements"],
  ["beneficiaries", "Beneficiaries"],
  ["payouts", "Payouts"],
  ["connectors", "Connectors"],
  ["inbound-webhooks", "Inbound webhooks"],
  ["outbound-webhooks", "Outbound webhooks"],
  ["dead-letters", "Dead letters"],
  ["reconciliation", "Reconciliation"],
  ["api-keys", "API keys"],
  ["audit-logs", "Audit logs"],
  ["usage", "Usage"],
  ["operational-metrics", "Operational metrics"],
] as const;

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return "Structured data";
  return String(value).replaceAll("_", " ");
}

export default async function OperationsWorkspace({
  searchParams,
}: {
  searchParams: Promise<{ environment?: string; resource?: string; after?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/operations");
  const query = await searchParams;
  const environmentsResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentsResponse.ok
    ? ((await environmentsResponse.json()) as Environment[])
    : [];
  const environment = environments.find((item) => item.id === query.environment) ?? environments[0];
  const resource = resources.some(([key]) => key === query.resource)
    ? query.resource!
    : resources[0][0];

  let page: OperationsPage = { data: [], nextCursor: null };
  let error: string | null = null;
  if (environment) {
    const params = new URLSearchParams({ limit: "25" });
    if (query.after) params.set("after", query.after);
    const response = await backendFetch(
      `/api/admin/v1/environments/${environment.id}/operations/${resource}?${params}`,
    );
    if (response.ok) page = (await response.json()) as OperationsPage;
    else error = "This operational view is temporarily unavailable.";
  }

  const columns = Array.from(
    new Set(page.data.flatMap((item) => Object.keys(item).filter((key) => key !== "details"))),
  ).slice(0, 6);

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Tenant-scoped control plane</p>
            <h1>Operations workspace</h1>
            <p>Inspect synthetic payment state without exposing credentials or raw request bytes.</p>
          </div>
          <form className="environment-picker" method="get">
            <input type="hidden" name="resource" value={resource} />
            <label htmlFor="environment">Environment</label>
            <select id="environment" name="environment" defaultValue={environment?.id}>
              {environments.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name} · {item.type.replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <button className="button button-secondary" type="submit">Apply scope</button>
          </form>
        </header>

        <div className="operations-layout">
          <nav className="resource-nav" aria-label="Operational resources">
            {resources.map(([key, label]) => (
              <Link
                key={key}
                href={`/operations?environment=${environment?.id ?? ""}&resource=${key}`}
                aria-current={resource === key ? "page" : undefined}
              >
                {label}
              </Link>
            ))}
          </nav>

          <section className="resource-panel" aria-labelledby="resource-title">
            <div className="resource-heading">
              <div>
                <p className="section-kicker">Environment record stream</p>
                <h2 id="resource-title">
                  {resources.find(([key]) => key === resource)?.[1]}
                </h2>
              </div>
              <span className="record-count">{page.data.length} records on this page</span>
            </div>
            {error ? <div className="callout callout-danger" role="alert">{error}</div> : null}
            {!environment ? (
              <div className="resource-empty" role="status">
                <h3>No active environments</h3>
                <p>Create an organisation environment before opening operational views.</p>
              </div>
            ) : null}
            {environment && !error && page.data.length === 0 ? (
              <div className="resource-empty" role="status">
                <h3>No {resources.find(([key]) => key === resource)?.[1].toLowerCase()}</h3>
                <p>This scoped synthetic environment has no matching records yet.</p>
              </div>
            ) : null}
            {page.data.length > 0 ? (
              <div className="table-scroll" tabIndex={0} aria-label="Scrollable operational records">
                <table className="operations-table">
                  <thead><tr>{columns.map((column) => <th scope="col" key={column}>{column}</th>)}<th scope="col">Evidence</th></tr></thead>
                  <tbody>
                    {page.data.map((item, index) => (
                      <tr key={String(item.id ?? index)}>
                        {columns.map((column) => <td key={column}>{displayValue(item[column])}</td>)}
                        <td><SafeJson value={item} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            <div className="pagination-actions">
              {query.after ? (
                <Link className="button button-secondary" href={`/operations?environment=${environment?.id}&resource=${resource}`}>First page</Link>
              ) : <span />}
              {page.nextCursor ? (
                <Link className="button button-primary" href={`/operations?environment=${environment?.id}&resource=${resource}&after=${encodeURIComponent(page.nextCursor)}`}>Next page</Link>
              ) : null}
            </div>
          </section>
        </div>
      </main>
    </ConsoleShell>
  );
}
