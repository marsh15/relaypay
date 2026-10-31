import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Ledger journals" };

type Environment = { id: string; name: string; type: string; status: string };
type Journal = {
  id: string;
  journalType: string;
  referenceType: string;
  currency: string;
  postedAt: string;
  createdAt: string;
};
type JournalsPage = { data: Journal[]; nextCursor: string | null };

export default async function LedgerWorkspace({
  searchParams,
}: {
  searchParams: Promise<{ environment?: string; after?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/ledger");
  const query = await searchParams;
  const environmentsResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentsResponse.ok
    ? ((await environmentsResponse.json()) as Environment[])
    : [];
  const environment = environments.find((item) => item.id === query.environment) ?? environments[0];

  let page: JournalsPage = { data: [], nextCursor: null };
  let error: string | null = null;
  if (environment) {
    const params = new URLSearchParams({ limit: "25" });
    if (query.after) params.set("after", query.after);
    const response = await backendFetch(
      `/api/admin/v1/environments/${encodeURIComponent(environment.id)}/operations/journals?${params}`,
    );
    if (response.ok) page = (await response.json()) as JournalsPage;
    else error = "Ledger journals are temporarily unavailable.";
  }

  const scope = `environment=${encodeURIComponent(environment?.id ?? "")}`;

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Immutable double-entry ledger</p>
            <h1>Ledger journals</h1>
            <p>Read-only journals. Open one to see every posting and its debit and credit totals.</p>
          </div>
          <form className="environment-picker" method="get">
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

        <section className="resource-panel" aria-labelledby="journals-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Posted journals</p>
              <h2 id="journals-title">Journals</h2>
            </div>
            <span className="record-count">{page.data.length} journals on this page</span>
          </div>
          {error ? <div className="callout callout-danger" role="alert">{error}</div> : null}
          {!environment ? (
            <div className="resource-empty" role="status">
              <h3>No active environments</h3>
              <p>Create an organisation environment before opening the ledger.</p>
            </div>
          ) : null}
          {environment && !error && page.data.length === 0 ? (
            <div className="resource-empty" role="status">
              <h3>No journals</h3>
              <p>This scoped synthetic environment has no posted journals yet.</p>
            </div>
          ) : null}
          {page.data.length > 0 ? (
            <div className="table-scroll" tabIndex={0} aria-label="Scrollable journals table">
              <table className="operations-table">
                <caption>Posted journals, newest first</caption>
                <thead>
                  <tr>
                    <th scope="col">Journal</th>
                    <th scope="col">Type</th>
                    <th scope="col">Reference</th>
                    <th scope="col">Currency</th>
                    <th scope="col">Posted</th>
                  </tr>
                </thead>
                <tbody>
                  {page.data.map((journal) => (
                    <tr key={journal.id}>
                      <td>
                        <Link href={`/ledger/${encodeURIComponent(journal.id)}?${scope}`}>
                          <code>{journal.id}</code>
                        </Link>
                      </td>
                      <td>{journal.journalType.replaceAll("_", " ")}</td>
                      <td>{journal.referenceType.replaceAll("_", " ")}</td>
                      <td>{journal.currency}</td>
                      <td>{new Date(journal.postedAt).toLocaleString("en-IN")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <div className="pagination-actions">
            {query.after ? (
              <Link className="button button-secondary" href={`/ledger?${scope}`}>
                First page
              </Link>
            ) : (
              <span />
            )}
            {page.nextCursor ? (
              <Link
                className="button button-primary"
                href={`/ledger?${scope}&after=${encodeURIComponent(page.nextCursor)}`}
              >
                Next page
              </Link>
            ) : null}
          </div>
        </section>
      </main>
    </ConsoleShell>
  );
}
