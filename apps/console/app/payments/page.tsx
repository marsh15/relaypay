import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { Money, StateBadge } from "@/components/evidence/shared";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Payments" };

type Environment = { id: string; name: string; type: string; status: string };
type Payment = {
  id: string;
  merchantReference: string;
  amount: number;
  currency: string;
  status: string;
  createdAt: string;
};
type PaymentsPage = { data: Payment[]; nextCursor: string | null };

const statuses = [
  "created",
  "requires_action",
  "authorised",
  "captured",
  "partially_refunded",
  "refunded",
  "failed",
  "expired",
  "cancelled",
] as const;

export default async function PaymentsWorkspace({
  searchParams,
}: {
  searchParams: Promise<{ environment?: string; status?: string; after?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/payments");
  const query = await searchParams;
  const environmentsResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentsResponse.ok
    ? ((await environmentsResponse.json()) as Environment[])
    : [];
  const environment = environments.find((item) => item.id === query.environment) ?? environments[0];
  const status = statuses.find((item) => item === query.status);

  let page: PaymentsPage = { data: [], nextCursor: null };
  let error: string | null = null;
  if (environment) {
    const params = new URLSearchParams({ limit: "25" });
    if (status) params.set("status", status);
    if (query.after) params.set("after", query.after);
    const response = await backendFetch(
      `/api/admin/v1/environments/${encodeURIComponent(environment.id)}/operations/payments?${params}`,
    );
    if (response.ok) page = (await response.json()) as PaymentsPage;
    else error = "Payments are temporarily unavailable.";
  }

  const scope = `environment=${encodeURIComponent(environment?.id ?? "")}`;
  const filter = status ? `&status=${status}` : "";

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Tenant-scoped payment intents</p>
            <h1>Payments</h1>
            <p>Read-only list of synthetic payment intents. Open one for its full evidence trail.</p>
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

        <section className="resource-panel" aria-labelledby="payments-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Payment intents</p>
              <h2 id="payments-title">Payments</h2>
            </div>
            <form className="environment-picker" method="get" aria-label="Filter payments by status">
              {environment ? <input type="hidden" name="environment" value={environment.id} /> : null}
              <label htmlFor="status">Status</label>
              <select id="status" name="status" defaultValue={status ?? ""}>
                <option value="">All statuses</option>
                {statuses.map((item) => (
                  <option value={item} key={item}>
                    {item.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
              <button className="button button-secondary" type="submit">Apply filter</button>
            </form>
            <span className="record-count">{page.data.length} payments on this page</span>
          </div>
          {error ? <div className="callout callout-danger" role="alert">{error}</div> : null}
          {!environment ? (
            <div className="resource-empty" role="status">
              <h3>No active environments</h3>
              <p>Create an organisation environment before opening payments.</p>
            </div>
          ) : null}
          {environment && !error && page.data.length === 0 ? (
            <div className="resource-empty" role="status">
              <h3>No payments</h3>
              <p>No payment intents match this scope and status filter.</p>
            </div>
          ) : null}
          {page.data.length > 0 ? (
            <div className="table-scroll" tabIndex={0} aria-label="Scrollable payments table">
              <table className="operations-table">
                <caption>Payment intents, newest first</caption>
                <thead>
                  <tr>
                    <th scope="col">Merchant reference</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="numeric">Amount</th>
                    <th scope="col">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {page.data.map((payment) => (
                    <tr key={payment.id}>
                      <td>
                        <Link href={`/payments/${encodeURIComponent(payment.id)}`}>
                          {payment.merchantReference}
                        </Link>
                        <div>
                          <code>{payment.id}</code>
                        </div>
                      </td>
                      <td>
                        <StateBadge state={payment.status} />
                      </td>
                      <td className="numeric">
                        <Money paise={payment.amount} />
                      </td>
                      <td>{new Date(payment.createdAt).toLocaleString("en-IN")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <div className="pagination-actions">
            {query.after ? (
              <Link className="button button-secondary" href={`/payments?${scope}${filter}`}>
                First page
              </Link>
            ) : (
              <span />
            )}
            {page.nextCursor ? (
              <Link
                className="button button-primary"
                href={`/payments?${scope}${filter}&after=${encodeURIComponent(page.nextCursor)}`}
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
