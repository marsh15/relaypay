"use client";

import { useRef, useState } from "react";

export function ReplayDeliveryButton({
  deliveryId,
  status,
  csrfToken,
}: {
  deliveryId: string;
  status: string;
  csrfToken: string;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const [message, setMessage] = useState<string | null>(null);
  if (!["DELIVERED", "DEAD_LETTER"].includes(status)) return null;

  async function replay() {
    const response = await fetch(`/backend/api/v1/webhook_deliveries/${deliveryId}/replay`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: "{}",
    });
    setMessage(
      response.ok
        ? "Replay scheduled with the original immutable event and endpoint version."
        : "Replay could not be scheduled.",
    );
    dialogRef.current?.close();
    openerRef.current?.focus();
  }

  return (
    <div className="admin-action">
      <button
        ref={openerRef}
        className="button button-secondary"
        type="button"
        onClick={() => dialogRef.current?.showModal()}
      >
        Replay delivery
      </button>
      {message ? <p role="status">{message}</p> : null}
      <dialog ref={dialogRef} aria-labelledby={`replay-title-${deliveryId}`}>
        <div className="dialog-content">
          <h3 id={`replay-title-${deliveryId}`}>Replay this delivery?</h3>
          <p>
            RelayPay will create a linked execution using the exact stored event bytes and captured
            endpoint version. The original attempt history remains unchanged.
          </p>
          <div className="dialog-actions">
            <button className="button button-secondary" type="button" onClick={() => dialogRef.current?.close()}>
              Cancel
            </button>
            <button className="button button-primary" type="button" onClick={replay}>
              Confirm replay
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
