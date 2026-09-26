import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { backendFetch, getSession } from "@/lib/server-api";
import { RiskActions } from "./actions";

export const metadata: Metadata = { title: "Risk review" };

type Version = {
  id: string;
  version: number;
  totalScore: number;
  completeness: number;
  domain: number;
  category: number;
  pricing: number;
  claims: number;
  severity: string;
  confidence: string;
  hardStop: boolean;
  reviewSha256: string;
};
type Finding = {
  id: string;
  type: string;
  checkKey: string;
  classification: string;
  severity: string;
  confidence: string;
  detail: string;
  quote: string | null;
  sourcePath: string | null;
  snapshotSha256: string;
  hardStop: boolean;
};
type Escalation = {
  id: string;
  reason: string;
  status: string;
  disposition: string | null;
  dispositionNote: string | null;
  dispositionedAt: string | null;
};
type Review = {
  id: string;
  status: string;
  siteRef: string;
  snapshotSha256: string | null;
  scoreVersion: number;
  versions: Version[];
  findings: Finding[];
  escalation: Escalation | null;
};

export default async function RiskReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ reviewId: string }>;
  searchParams: Promise<{ environment?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/risk");
  const [{ reviewId }, query] = await Promise.all([params, searchParams]);
  if (!query.environment) notFound();
  const response = await backendFetch(
    `/api/admin/v1/environments/${query.environment}/risk-reviews/${reviewId}`,
  );
  if (!response.ok) notFound();
  const item = (await response.json()) as Review;
  const latest = item.versions[item.versions.length - 1];

  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Immutable risk evidence</p>
            <h1>{item.siteRef.replaceAll("_", " ").toLowerCase()}</h1>
            <p>
              {item.status.replaceAll("_", " ").toLowerCase()} · score version{" "}
              {item.scoreVersion} · snapshot {item.snapshotSha256?.slice(0, 12)}…
            </p>
          </div>
        </header>
        {latest ? (
          <section className="resource-panel" aria-labelledby="risk-score-title">
            <div className="resource-heading">
              <div>
                <p className="section-kicker">Versioned, reproducible</p>
                <h2 id="risk-score-title">Score breakdown</h2>
              </div>
            </div>
            <div className="table-scroll" tabIndex={0} aria-label="Score breakdown">
              <table className="operations-table">
                <thead>
                  <tr>
                    <th scope="col">Version</th>
                    <th scope="col">Completeness</th>
                    <th scope="col">Domain</th>
                    <th scope="col">Category</th>
                    <th scope="col">Pricing</th>
                    <th scope="col">Claims</th>
                    <th scope="col">Total risk</th>
                    <th scope="col">Severity</th>
                    <th scope="col">Confidence</th>
                    <th scope="col">Hard stop</th>
                  </tr>
                </thead>
                <tbody>
                  {item.versions.map((version) => (
                    <tr key={version.id}>
                      <td>{version.version}</td>
                      <td>{version.completeness}/20</td>
                      <td>{version.domain}/20</td>
                      <td>{version.category}/30</td>
                      <td>{version.pricing}/15</td>
                      <td>{version.claims}/15</td>
                      <td>{version.totalScore}/100</td>
                      <td>{version.severity}</td>
                      <td>{version.confidence}</td>
                      <td>{version.hardStop ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="supporting-copy">
              Review digest {latest.reviewSha256.slice(0, 24)}…
            </p>
          </section>
        ) : null}
        <section className="resource-panel" aria-labelledby="risk-findings-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Deterministic checks and cited model findings</p>
              <h2 id="risk-findings-title">Findings</h2>
            </div>
            <span className="record-count">{item.findings.length} findings</span>
          </div>
          <div className="table-scroll" tabIndex={0} aria-label="Risk findings">
            <table className="operations-table">
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Classification</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Detail / quote</th>
                </tr>
              </thead>
              <tbody>
                {item.findings.map((finding) => (
                  <tr key={finding.id}>
                    <td>{finding.checkKey}</td>
                    <td>{finding.classification.replaceAll("_", " ").toLowerCase()}</td>
                    <td>{finding.severity}</td>
                    <td>{finding.confidence}</td>
                    <td>{finding.quote ?? finding.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        {item.escalation ? (
          <section className="resource-panel" aria-labelledby="risk-escalation-title">
            <div className="resource-heading">
              <div>
                <p className="section-kicker">Hard stop or severity escalation</p>
                <h2 id="risk-escalation-title">Escalation</h2>
              </div>
            </div>
            <p>
              {item.escalation.reason.replaceAll("_", " ").toLowerCase()} ·{" "}
              {item.escalation.status.replaceAll("_", " ").toLowerCase()}
              {item.escalation.disposition
                ? ` · ${item.escalation.disposition.replaceAll("_", " ").toLowerCase()}`
                : ""}
            </p>
          </section>
        ) : null}
        <RiskActions
          environmentId={query.environment}
          reviewId={item.id}
          csrfToken={session.csrfToken}
          escalationStatus={item.escalation?.status ?? null}
        />
      </main>
    </ConsoleShell>
  );
}
