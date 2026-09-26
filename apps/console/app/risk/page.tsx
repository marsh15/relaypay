import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { backendFetch, getSession } from "@/lib/server-api";
import { RiskReviewLauncher } from "./launcher";

export const metadata: Metadata = { title: "Merchant risk review" };

type Environment = { id: string; name: string; type: string };
type RiskReview = {
  id: string;
  siteRef: string;
  status: string;
  scoreVersion: number;
  createdAt: string;
};

export default async function RiskPage({
  searchParams,
}: {
  searchParams: Promise<{ environment?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/risk");
  const query = await searchParams;
  const environmentResponse = await backendFetch("/api/admin/v1/environments");
  const environments = environmentResponse.ok
    ? ((await environmentResponse.json()) as Environment[])
    : [];
  const environment =
    environments.find((item) => item.id === query.environment) ?? environments[0];
  const response = environment
    ? await backendFetch(`/api/admin/v1/environments/${environment.id}/risk-reviews`)
    : null;
  const reviews = response?.ok ? ((await response.json()) as RiskReview[]) : [];

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Immutable onboarding evidence</p>
            <h1>Merchant risk review</h1>
            <p>
              Deterministic checks, model claim findings with exact citations, and hard-stop
              escalations on synthetic merchant sites.
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
          <RiskReviewLauncher environmentId={environment.id} csrfToken={session.csrfToken} />
        ) : null}
        <section className="resource-panel" aria-labelledby="risk-queue-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Newest first</p>
              <h2 id="risk-queue-title">Reviews</h2>
            </div>
            <span className="record-count">{reviews.length} reviews</span>
          </div>
          {reviews.length === 0 ? (
            <div className="resource-empty" role="status">
              <h3>No reviews yet</h3>
              <p>Start a review for one of the synthetic merchant sites above.</p>
            </div>
          ) : (
            <div className="table-scroll" tabIndex={0} aria-label="Scrollable risk reviews">
              <table className="operations-table">
                <thead>
                  <tr>
                    <th scope="col">Review</th>
                    <th scope="col">Site</th>
                    <th scope="col">State</th>
                    <th scope="col">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {reviews.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <Link href={`/risk/${item.id}?environment=${environment?.id}`}>
                          {item.id}
                        </Link>
                      </td>
                      <td>{item.siteRef.replaceAll("_", " ").toLowerCase()}</td>
                      <td>{item.status.replaceAll("_", " ").toLowerCase()}</td>
                      <td>{new Date(item.createdAt).toLocaleString()}</td>
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
