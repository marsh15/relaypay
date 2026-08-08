"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

type Draft = { classification: string; confidence: string; responseText: string; selectedEvidence: Array<{recordType:string;recordId:string;fieldPaths:string[];snapshotSha256:string}>; missingEvidence: string[] };
type Package = { id: string; status: string; approvalRequestId: string | null; approvalStatus: string | null };

export function DisputeActions({ environmentId, caseId, csrfToken, latestDraft, latestPackage }: { environmentId:string; caseId:string; csrfToken:string; latestDraft:Draft | null; latestPackage:Package | null }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function command(path:string, body?:unknown) {
    setBusy(true); setMessage("");
    const response = await fetch(`/backend/api/admin/v1/environments/${environmentId}${path}`, { method:"POST", headers:{"Content-Type":"application/json","X-CSRF-Token":csrfToken,"Idempotency-Key":crypto.randomUUID()}, body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await response.json().catch(() => ({})) as {error?:{message?:string}};
    setBusy(false); setMessage(response.ok ? "Saved. The immutable case history has been updated." : value.error?.message ?? "Request failed.");
    if (response.ok) router.refresh();
  }
  return <section className="resource-panel" aria-labelledby="case-actions-title"><div className="resource-heading"><div><p className="section-kicker">Controlled actions</p><h2 id="case-actions-title">Review and submit</h2></div></div>
    <div className="dispute-actions">
      <button className="button button-secondary" disabled={busy} onClick={() => command(`/disputes/${caseId}/agent-drafts`)}>Generate evidence draft</button>
      {latestDraft ? <form onSubmit={(event) => {event.preventDefault(); const data = new FormData(event.currentTarget); void command(`/disputes/${caseId}/drafts`, {...latestDraft, responseText:String(data.get("responseText"))});}}><label htmlFor="responseText">Analyst response</label><textarea id="responseText" name="responseText" rows={8} defaultValue={latestDraft.responseText} required maxLength={20000}/><p className="supporting-copy">Saving creates a new version and invalidates every prior approval.</p><button className="button button-secondary" disabled={busy} type="submit">Save immutable revision</button></form> : null}
      {latestDraft ? <button className="button button-primary" disabled={busy} onClick={() => command(`/disputes/${caseId}/packages`)}>Freeze exact package</button> : null}
      {latestPackage?.approvalRequestId && latestPackage.approvalStatus === "PENDING" ? <button className="button button-secondary" disabled={busy} onClick={() => command(`/approval-requests/${latestPackage.approvalRequestId}/decisions`, {decision:"APPROVED", note:"Exact package reviewed"})}>Approve exact package</button> : null}
      {latestPackage?.status === "APPROVED" ? <button className="button button-primary" disabled={busy} onClick={() => command(`/dispute-packages/${latestPackage.id}/submit`)}>Submit approved bytes</button> : null}
      {latestPackage ? <a className="button button-secondary" href={`/backend/api/admin/v1/environments/${environmentId}/dispute-packages/${latestPackage.id}/download`}>Download frozen ZIP</a> : null}
    </div>{message ? <p role="status" aria-live="polite">{message}</p> : null}
  </section>;
}
