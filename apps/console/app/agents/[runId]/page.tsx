import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { SafeJson } from "@/components/evidence/safe-json";
import { backendFetch, getSession } from "@/lib/server-api";

export const metadata: Metadata = { title: "Agent run trace" };

type RunTrace = {
  id: string;
  status: string;
  route: string;
  steps: Array<{ id: string; key: string; kind: string; status: string; attemptCount: number; safeErrorCode: string | null }>;
  artifacts: Array<{ id: string; type: string; version: number; sha256: string; byteLength: number }>;
  approvals: Array<{ id: string; artifactSha256: string; status: string }>;
  deadLetters: Array<{ id: string; reasonCode: string; replayCount: number; evidence: unknown }>;
};

export default async function AgentRunTrace({ params, searchParams }: { params: Promise<{ runId: string }>; searchParams: Promise<{ environment?: string }> }) {
  const session = await getSession();
  if (!session) redirect("/login?next=/agents");
  const [{ runId }, query] = await Promise.all([params, searchParams]);
  if (!query.environment) notFound();
  const response = await backendFetch(`/api/admin/v1/environments/${query.environment}/workflow-runs/${runId}`);
  if (!response.ok) notFound();
  const run = (await response.json()) as RunTrace;
  return <ConsoleShell session={session}><main id="main-content" className="page page-operations"><header className="operations-header"><div><p className="eyebrow">Immutable execution evidence</p><h1>Agent run trace</h1><p>{run.id} · {run.route} · {run.status.replaceAll("_", " ")}</p></div></header><section className="resource-panel"><div className="resource-heading"><div><p className="section-kicker">Lease and retry history</p><h2>Steps</h2></div></div><SafeJson value={run.steps} /></section><section className="resource-panel"><div className="resource-heading"><div><p className="section-kicker">Digest-addressed outputs</p><h2>Artifacts and approvals</h2></div></div><SafeJson value={{ artifacts: run.artifacts, approvals: run.approvals }} /></section><section className="resource-panel"><div className="resource-heading"><div><p className="section-kicker">Audited recovery queue</p><h2>Dead letters</h2></div></div><SafeJson value={run.deadLetters} /></section></main></ConsoleShell>;
}
