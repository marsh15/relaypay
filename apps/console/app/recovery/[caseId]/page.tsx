import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { SafeJson } from "@/components/evidence/safe-json";
import { backendFetch, getSession } from "@/lib/server-api";
import { RecoveryActions } from "./recovery-actions";

export const metadata: Metadata = { title: "Recovery case" };

type Action = {
  id: string;
  sequence: number;
  type: "PAYMENT_RETRY" | "MESSAGE";
  channel: string | null;
  status: string;
  scheduledFor: string;
  due: boolean;
  payloadSha256: string;
  responseCode: string | null;
};
type RecoveryCase = {
  id: string;
  status: string;
  classification: string;
  terminationReason: string | null;
  subscription: { id: string; planReference: string; consentSha256: string };
  invoice: { id: string; amount: number; currency: string; status: string };
  actions: Action[];
};

export default async function RecoveryCasePage({
  params,
  searchParams,
}: {
  params: Promise<{ caseId: string }>;
  searchParams: Promise<{ environment?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/login?next=/recovery");
  const [{ caseId }, query] = await Promise.all([params, searchParams]);
  if (!query.environment) notFound();
  const response = await backendFetch(
    `/api/admin/v1/environments/${query.environment}/recovery-cases/${caseId}`,
  );
  if (!response.ok) notFound();
  const item = (await response.json()) as RecoveryCase;
  return (
    <ConsoleShell session={session}>
      <main id="main-content" className="page page-operations">
        <header className="operations-header">
          <div>
            <p className="eyebrow">Terminal-safe recovery case</p>
            <h1>{item.id}</h1>
            <p>
              {item.classification.replaceAll("_", " ")} · {item.status.replaceAll("_", " ")} ·{" "}
              {item.invoice.currency} {(item.invoice.amount / 100).toFixed(2)}
            </p>
          </div>
        </header>
        <RecoveryActions
          environmentId={query.environment}
          caseId={item.id}
          csrfToken={session.csrfToken}
          status={item.status}
          actions={item.actions.map(({ id, type, status, scheduledFor, due }) => ({
            id,
            type,
            status,
            scheduledFor,
            due,
          }))}
        />
        <section className="resource-panel" aria-labelledby="scheduled-actions-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Immutable policy decisions</p>
              <h2 id="scheduled-actions-title">Scheduled actions</h2>
            </div>
          </div>
          {item.actions.length === 0 ? (
            <div className="resource-empty" role="status">
              <h3>No actions authorized</h3>
              <p>The failure classification or consent policy required an immediate stop.</p>
            </div>
          ) : (
            <div className="table-scroll" tabIndex={0} aria-label="Scrollable recovery actions">
              <table className="operations-table">
                <thead>
                  <tr>
                    <th scope="col">Sequence</th>
                    <th scope="col">Action</th>
                    <th scope="col">State</th>
                    <th scope="col">Scheduled</th>
                    <th scope="col">Provider result</th>
                  </tr>
                </thead>
                <tbody>
                  {item.actions.map((action) => (
                    <tr key={action.id}>
                      <td>{action.sequence}</td>
                      <td>
                        {action.type.replaceAll("_", " ")}
                        {action.channel ? ` · ${action.channel}` : ""}
                      </td>
                      <td>{action.status.replaceAll("_", " ")}</td>
                      <td>{new Date(action.scheduledFor).toLocaleString()}</td>
                      <td>{action.responseCode ?? "Not executed"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="resource-panel" aria-labelledby="recovery-evidence-title">
          <div className="resource-heading">
            <div>
              <p className="section-kicker">Digest-addressed authority</p>
              <h2 id="recovery-evidence-title">Case evidence</h2>
            </div>
          </div>
          <SafeJson value={item} />
        </section>
      </main>
    </ConsoleShell>
  );
}
