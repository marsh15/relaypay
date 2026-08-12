import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Subscription recovery" };

type Environment = { id: string; name: string; type: string };
type RecoveryCase = {
  id: string;
  status: string;
  classification: string;
  paymentRetryCount: number;
  messageCount: number;
  expiresAt: string;
};

export default async function RecoveryPage({
  searchParams,
}: {
  searchParams: Promise<{ environment?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/recovery");
  const query = await searchParams;
  const environmentResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentResponse.ok
    ? ((await environmentResponse.json()) as Environment[])
    : [];
  const environment =
    environments.find((item) => item.id === query.environment) ?? environments[0];
  const response = environment
    ? await backendFetch(`/api/admin/v1/environments/${environment.id}/recovery-cases`)
    : null;
  const cases = response?.ok ? ((await response.json()) as RecoveryCase[]) : [];

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Consent-bound revenue operations</p>
            <h1>Subscription recovery</h1>
            <p>
              Inspect bounded retries, synthetic messages, ambiguous outcomes, and terminal stops.
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
        <section className="resource-panel" aria-labelledby="recovery-queue-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Earliest expiry first</p>
              <h2 id="recovery-queue-title">Recovery cases</h2>
            </div>
            <span className="record-count">{cases.length} cases</span>
          </div>
          {cases.length === 0 ? (
            <div className="resource-empty" role="status">
              <h3>No recovery cases</h3>
              <p>Verified synthetic recurring-payment failures will appear here.</p>
            </div>
          ) : (
            <div className="table-scroll" tabIndex={0} aria-label="Scrollable recovery cases">
              <table className="operations-table">
                <thead>
                  <tr>
                    <th scope="col">Case</th>
                    <th scope="col">Classification</th>
                    <th scope="col">State</th>
                    <th scope="col">Effects</th>
                    <th scope="col">Expires</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <Link href={`/recovery/${item.id}?environment=${environment?.id}`}>
                          {item.id}
                        </Link>
                      </td>
                      <td>{item.classification.replaceAll("_", " ")}</td>
                      <td>{item.status.replaceAll("_", " ")}</td>
                      <td>
                        {item.paymentRetryCount} retries · {item.messageCount} messages
                      </td>
                      <td>{new Date(item.expiresAt).toLocaleDateString()}</td>
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
