"use client";

import { useRef, useState } from "react";

export function RetryLookupButton({ operationId, csrfToken }: { operationId: string; csrfToken: string }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function retry() {
    const response = await fetch(`/backend/api/v1/operations/${operationId}/retry_lookup`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: "{}",
    });
    setStatus(response.ok ? "Status lookup requested. Mutation was not resent." : "Lookup is unavailable.");
    dialogRef.current?.close();
    openerRef.current?.focus();
  }

  return (
    <div className="admin-action">
      <button ref={openerRef} className="button button-secondary" type="button" onClick={() => dialogRef.current?.showModal()}>
        Retry status lookup
      </button>
      {status ? <p role="status">{status}</p> : null}
      <dialog ref={dialogRef} aria-labelledby={`lookup-title-${operationId}`} onCancel={() => openerRef.current?.focus()}>
        <div className="dialog-content">
          <h3 id={`lookup-title-${operationId}`}>Query provider status?</h3>
          <p>This action performs a status-only lookup using the stable key. It never repeats the provider mutation.</p>
          <div className="dialog-actions">
            <button className="button button-secondary" type="button" onClick={() => dialogRef.current?.close()}>
              Cancel
            </button>
            <button className="button button-primary" type="button" onClick={retry}>
              Confirm lookup
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
