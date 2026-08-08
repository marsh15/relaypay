import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { ConsoleShell } from "@/components/console-shell";
import { SafeJson } from "@/components/evidence/safe-json";
import { backendFetch, getSession } from "@/lib/server-api";
import { DisputeActions } from "./dispute-actions";

export const metadata: Metadata = { title: "Dispute case" };
type Draft = { id:string; version:number; authorType:string; classification:string; confidence:string; responseText:string; selectedEvidence:Array<{recordType:string;recordId:string;fieldPaths:string[];snapshotSha256:string}>; missingEvidence:string[]; sha256:string };
type Package = { id:string; version:number; status:string; sha256:string; byteLength:number; approvalRequestId:string|null; approvalStatus:string|null };
type Case = { id:string; networkDisputeId:string; reasonCode:string; amount:number; currency:string; status:string; dueAt:string; sourceSha256:string; drafts:Draft[]; packages:Package[] };

export default async function DisputeCasePage({params,searchParams}:{params:Promise<{caseId:string}>;searchParams:Promise<{environment?:string}>}) {
  const session = await getSession(); if (!session) redirect("/login?next=/disputes");
  const [{caseId},query] = await Promise.all([params,searchParams]); if (!query.environment) notFound();
  const response = await backendFetch(`/api/admin/v1/environments/${query.environment}/disputes/${caseId}`); if (!response.ok) notFound();
  const item = await response.json() as Case; const latestDraft=item.drafts[0]??null; const latestPackage=item.packages[0]??null;
  return <ConsoleShell session={session}><main id="main-content" className="page page-operations"><header className="operations-header"><div><p className="eyebrow">Immutable dispute case</p><h1>{item.networkDisputeId}</h1><p>{item.reasonCode.replaceAll("_"," ")} · {item.currency} {(item.amount/100).toFixed(2)} · {item.status.replaceAll("_"," ")}</p></div></header>
    <DisputeActions environmentId={query.environment} caseId={item.id} csrfToken={session.csrfToken} latestDraft={latestDraft} latestPackage={latestPackage}/>
    <section className="resource-panel"><div className="resource-heading"><div><p className="section-kicker">Explicit selected and missing evidence</p><h2>Latest draft</h2></div></div>{latestDraft ? <SafeJson value={latestDraft}/> : <div className="resource-empty" role="status"><h3>No draft yet</h3><p>Generate a deterministic draft from allowlisted evidence.</p></div>}</section>
    <section className="resource-panel"><div className="resource-heading"><div><p className="section-kicker">Digest-bound versions</p><h2>Packages</h2></div></div><SafeJson value={{sourceSha256:item.sourceSha256,packages:item.packages}}/></section>
  </main></ConsoleShell>;
}
